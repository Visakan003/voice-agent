import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
import aiohttp.resolver as aiohttp_resolver
import aiohttp.connector as aiohttp_connector
from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents import worker as livekit_worker
from livekit.agents.worker import JobExecutorType
from livekit.plugins import openai
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

# Topic for booking details data messages
BOOKING_DATA_TOPIC = "booking_details"

# Per-room storage
_room_booking_store: dict[str, dict[str, str]] = {}

# Email format validation
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Calendly API
CALENDLY_API_BASE = "https://api.calendly.com"
_calendly_event_type_uri_cache: str | None = None

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-assistant")

# Load knowledge base
KNOWLEDGE_FILE = Path(__file__).parent / "knowledge.txt"
KNOWLEDGE_PROMPT = KNOWLEDGE_FILE.read_text(encoding="utf-8")

# Force threaded DNS resolver to avoid aiodns issues
if os.getenv("LIVEKIT_FORCE_THREADED_DNS", "1").strip().lower() in {"1", "true", "yes"}:
    aiohttp_resolver.DefaultResolver = aiohttp_resolver.ThreadedResolver
    aiohttp_connector.DefaultResolver = aiohttp_resolver.ThreadedResolver
    logger.info("Using threaded DNS resolver")

# Patch assignment URL
_ORIGINAL_HANDLE_ASSIGNMENT = livekit_worker.AgentServer._handle_assignment


def _patched_handle_assignment(self, assignment):
    force_base = os.getenv("LIVEKIT_FORCE_BASE_URL", "1").strip().lower() in {"1", "true", "yes"}
    base_url = (os.getenv("LIVEKIT_URL") or "").strip()
    assigned = getattr(assignment, "url", "") or ""
    if force_base and base_url and assigned and assigned != base_url:
        logger.warning("Overriding assignment URL %s -> %s", assigned, base_url)
        assignment.url = base_url
    return _ORIGINAL_HANDLE_ASSIGNMENT(self, assignment)


livekit_worker.AgentServer._handle_assignment = _patched_handle_assignment


async def _resolve_calendly_event_type_uri(token: str) -> str | None:
    """Resolve event type URI from env or scheduling link."""
    global _calendly_event_type_uri_cache
    if _calendly_event_type_uri_cache:
        return _calendly_event_type_uri_cache
    event_type_uri = (os.getenv("CALENDLY_EVENT_TYPE_URI") or "").strip()
    if event_type_uri and event_type_uri.startswith("http"):
        _calendly_event_type_uri_cache = event_type_uri
        return event_type_uri
    scheduling_link = (os.getenv("CALENDLY_SCHEDULING_LINK") or "").strip()
    if not scheduling_link or "calendly.com/" not in scheduling_link:
        return event_type_uri or None
    slug = scheduling_link.rstrip("/").split("/")[-1] if "/" in scheduling_link else ""
    if not slug:
        return event_type_uri or None
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {token}"}
            async with session.get(f"{CALENDLY_API_BASE}/users/me", headers=headers) as resp:
                if resp.status != 200:
                    return event_type_uri or None
                me = await resp.json()
            user_uri = (me.get("resource") or me).get("uri") or (me.get("uri"))
            if not user_uri:
                return event_type_uri or None
            async with session.get(
                f"{CALENDLY_API_BASE}/event_types",
                headers=headers,
                params={"user": user_uri},
            ) as resp:
                if resp.status != 200:
                    return event_type_uri or None
                data = await resp.json()
            collection = data.get("collection") or data.get("items") or []
            for et in collection:
                r = et.get("resource") or et
                if (r.get("slug") or "").strip() == slug or (r.get("uri") or "").endswith("/" + slug):
                    uri = (r.get("uri") or "").strip()
                    if uri:
                        _calendly_event_type_uri_cache = uri
                        return uri
    except Exception as e:
        logger.warning("Calendly resolve failed: %s", e)
    return event_type_uri or None


def _normalize_calendly_location_kind_from_env(raw: str) -> str:
    """Map env value to kind."""
    if not raw:
        return raw
    k = raw.strip().lower()
    if k in ("custom", "custom_link", "zoom_conference", "google_meet", "microsoft_teams", "phone_call", "in_person", "outbound_call", "ask_invitee"):
        return k
    if "zoom" in k:
        return "zoom_conference"
    if "google" in k or "meet" in k:
        return "google_meet"
    if "teams" in k or "microsoft" in k:
        return "microsoft_teams"
    if "phone" in k or "call" in k:
        return "phone_call"
    if "person" in k:
        return "in_person"
    if "custom" in k or "link" in k or "conference" in k:
        return "custom_link"
    return k


async def _get_calendly_location_kinds_for_event(token: str, event_type_uri: str) -> list[str]:
    """Get location kinds from event type."""
    if not event_type_uri or not token:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                event_type_uri,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        resource = data.get("resource") or data
        locations = resource.get("locations") or []
        if not locations and "location" in resource:
            locations = [resource["location"]]
        kinds: list[str] = []
        for loc in locations:
            if not isinstance(loc, dict):
                kind = str(loc).strip() if loc else ""
            else:
                kind = (
                    loc.get("kind")
                    or loc.get("type")
                    or (loc.get("location") if isinstance(loc.get("location"), str) else "")
                    or ""
                ).strip()
            if kind and kind not in kinds:
                kinds.append(kind)
        return kinds
    except Exception:
        return []


async def _fetch_calendly_availability(days_ahead: int = 7) -> list[dict]:
    """Fetch available time slots from Calendly."""
    token = (os.getenv("CALENDLY_ACCESS_TOKEN") or "").strip()
    if not token:
        return []
    event_type_uri = await _resolve_calendly_event_type_uri(token)
    if not event_type_uri:
        return []
    now = datetime.now(timezone.utc)
    start_time = now
    end_time = now + timedelta(days=min(days_ahead, 7))
    params = {
        "event_type": event_type_uri,
        "start_time": start_time.isoformat().replace("+00:00", "Z"),
        "end_time": end_time.isoformat().replace("+00:00", "Z"),
    }
    url = f"{CALENDLY_API_BASE}/event_type_available_times?{urlencode(params)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        collection = data.get("collection") or data.get("items") or []
        slots = []
        for s in collection:
            r = s.get("resource") or s
            start = r.get("start_time")
            if start:
                slots.append({"start": start, "end": r.get("end_time")})
        return slots
    except Exception:
        return []


@function_tool(
    description="Check calendar availability for the next few days."
)
async def check_calendly_availability(days_ahead: int = 7) -> str:
    """Fetch Calendly availability and return summary."""
    slots = await _fetch_calendly_availability(days_ahead=days_ahead)
    if not slots:
        return "No availability could be loaded. Ask user to visit booking link."
    lines = []
    for s in slots[:20]:
        start = s.get("start")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            human = dt.strftime("%A %B %d at %I:%M %p UTC")
            lines.append(f"{human} ({start})")
        except Exception:
            lines.append(start)
    if not lines:
        return "No availability could be loaded."
    return "Available times: " + "; ".join(lines) + ". Offer these to user."


async def _create_calendly_invitee(email: str, start_time_iso: str, invitee_timezone: str = "UTC") -> tuple[dict | None, str]:
    """Create Calendly invitee."""
    token = (os.getenv("CALENDLY_ACCESS_TOKEN") or "").strip()
    if not token:
        return None, "Calendly not configured."
    event_type_uri = await _resolve_calendly_event_type_uri(token)
    if not event_type_uri:
        return None, "Calendly event type not resolved."
    raw_env = (os.getenv("CALENDLY_LOCATION_KIND") or "").strip() or None
    if raw_env:
        location_kinds_to_try = [_normalize_calendly_location_kind_from_env(raw_env)]
    else:
        location_kinds_to_try = await _get_calendly_location_kinds_for_event(token, event_type_uri)
        if not location_kinds_to_try:
            location_kinds_to_try = [_normalize_calendly_location_kind_from_env(raw_env)] if raw_env else []
        if not location_kinds_to_try:
            return None, "No location configured."
    start_time_iso = start_time_iso.strip().replace("+00:00", "Z")
    if not start_time_iso.endswith("Z"):
        start_time_iso = start_time_iso + "Z"
    url = f"{CALENDLY_API_BASE}/invitees"
    last_err_msg = ""
    for location_kind in location_kinds_to_try:
        payload = {
            "event_type": event_type_uri,
            "start_time": start_time_iso,
            "invitee": {
                "email": email.strip(),
                "name": "Guest",
                "timezone": invitee_timezone,
            },
            "location": {
                "kind": location_kind
            }
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=payload,
                ) as resp:
                    body = await resp.text()
                    if resp.status in (200, 201):
                        return (json.loads(body) if body else {}, "")
                    err_msg = body
                    try:
                        data = json.loads(body)
                        err_msg = data.get("message") or data.get("error") or body
                    except Exception:
                        pass
                    last_err_msg = f"Calendly API returned {resp.status}"
                    if resp.status == 400 and ("location" in (err_msg or "").lower() or "invalid_location" in (err_msg or "").lower()):
                        continue
                    return None, last_err_msg
        except Exception as e:
            last_err_msg = str(e)
    return None, last_err_msg


def _make_book_calendly_meeting_tool(room):
    """Factory for booking tool."""

    @function_tool(
        description="Book meeting in Calendly for chosen time."
    )
    async def book_calendly_meeting(start_time: str, email: str = "") -> str:
        """Create Calendly invitee."""
        if not email or not email.strip():
            stored = _room_booking_store.get(room.name)
            if stored and stored.get("email"):
                email = stored["email"]
            else:
                return "No email stored. Ask user for email again."
        result, error_message = await _create_calendly_invitee(email, start_time)
        payload = json.dumps({
            "type": "meeting_booked",
            "booked": result is not None,
            "start_time": start_time,
            "email": email,
            "error": error_message if result is None else None,
        })
        await room.local_participant.publish_data(
            payload.encode("utf-8"),
            topic="meeting_booked",
            reliable=True,
        )
        if result:
            return "Meeting booked. User will receive confirmation email."
        return f"Booking failed: {error_message}"

    return book_calendly_meeting


def prewarm(proc: JobProcess):
    """Simple prewarm - NO ASYNC OPERATIONS."""
    logger.info("Worker prewarm started")
    # Store knowledge in userdata
    proc.userdata["knowledge"] = KNOWLEDGE_PROMPT
    logger.info("Worker prewarm complete")


async def entrypoint(ctx: JobContext):
    started_at = time.perf_counter()

    logger.info("Connecting to LiveKit room...")
    
    # Connect immediately with minimal options
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    logger.info("Connected to room (%.2fs)", time.perf_counter() - started_at)

    room = ctx.room

    # Define tools
    @function_tool(
        description="Save booking details after collecting email, phone, country."
    )
    async def store_booking_details(email: str, phone: str, country: str) -> str:
        email = (email or "").strip()
        if not email or not EMAIL_REGEX.match(email):
            return "Email format incorrect. Ask user to repeat."
        payload = json.dumps({
            "type": "booking_details",
            "email": email,
            "phone": phone,
            "country": country,
        })
        await room.local_participant.publish_data(
            payload.encode("utf-8"),
            topic=BOOKING_DATA_TOPIC,
            reliable=True,
        )
        _room_booking_store[room.name] = {"email": email, "phone": phone, "country": country}
        return "Booking details saved."

    @function_tool(
        description="Get stored booking details."
    )
    async def get_stored_booking_details() -> str:
        stored = _room_booking_store.get(room.name)
        if not stored:
            return "No details stored yet."
        return f"Stored: email {stored.get('email', '')}, phone {stored.get('phone', '')}, country {stored.get('country', '')}"

    # Create session with optimized settings
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime-1.5",
            voice="marin",
            speed=1.0,
            turn_detection=ServerVad(
                type="server_vad",
                threshold=0.5,
                silence_duration_ms=150,
                prefix_padding_ms=100,
                create_response=True,
                interrupt_response=True,
            ),
        ),
    )

    agent = Agent(
        instructions=KNOWLEDGE_PROMPT,
        tools=[store_booking_details, get_stored_booking_details, check_calendly_availability, _make_book_calendly_meeting_tool(room)],
    )

    # Start session and generate greeting
    logger.info("Starting agent session...")
    await session.start(agent=agent, room=ctx.room)
    
    logger.info("Generating greeting...")
    await session.generate_reply(
        instructions="Say exactly once: Hey there! I'm Zia from Atlasium. How can I help you today?"
    )
    
    logger.info("Ready (total time: %.2fs)", time.perf_counter() - started_at)

    # Keep alive
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    logger.info("Starting LiveKit voice agent worker...")
    
    # CRITICAL FIX: Reduce idle processes to 1 to avoid initialization loops
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            job_executor_type=JobExecutorType.PROCESS,
            num_idle_processes=1,  # Start with 1 to avoid initialization failures
        ),
    )