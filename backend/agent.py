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

# Topic for booking details data messages (frontend subscribes and saves to localStorage)
BOOKING_DATA_TOPIC = "booking_details"

# Per-room storage so the agent can use stored booking details in the conversation (e.g. when booking Calendly)
_room_booking_store: dict[str, dict[str, str]] = {}

# Email format validation before storing (voice capture can produce malformed strings)
# Must match something@something.something (literal dot in domain)
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Calendly API (optional): set CALENDLY_ACCESS_TOKEN and either CALENDLY_EVENT_TYPE_URI or CALENDLY_SCHEDULING_LINK in .env
CALENDLY_API_BASE = "https://api.calendly.com"
# Resolved event type URI when using CALENDLY_SCHEDULING_LINK (cached per process)
_calendly_event_type_uri_cache: str | None = None

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-assistant")

# Load knowledge base
KNOWLEDGE_FILE = Path(__file__).parent / "knowledge.txt"
KNOWLEDGE_PROMPT = KNOWLEDGE_FILE.read_text(encoding="utf-8")

# Production-safe DNS fix:
# aiodns can fail with "Could not contact DNS servers" on some hosts/networks.
# Force aiohttp to use the OS threaded resolver for LiveKit/OpenAI sockets.
if os.getenv("LIVEKIT_FORCE_THREADED_DNS", "1").strip().lower() in {"1", "true", "yes"}:
    aiohttp_resolver.DefaultResolver = aiohttp_resolver.ThreadedResolver
    aiohttp_connector.DefaultResolver = aiohttp_resolver.ThreadedResolver
    logger.info("Using threaded DNS resolver (LIVEKIT_FORCE_THREADED_DNS=1)")

# Force assignment URL to base LIVEKIT_URL (production-safe fallback for region routing issues).
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
    """Resolve event type URI from CALENDLY_EVENT_TYPE_URI or by looking up CALENDLY_SCHEDULING_LINK (e.g. https://calendly.com/username/event-slug)."""
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
    # Parse slug from URL: https://calendly.com/username/event-slug -> event-slug
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
        logger.warning("Calendly resolve event type from link failed: %s", e)
    return event_type_uri or None


def _normalize_calendly_location_kind_from_env(raw: str) -> str:
    """Map env value to kind. Only for CALENDLY_LOCATION_KIND. Common kinds: custom, custom_link, zoom_conference, google_meet."""
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
    # "Custom conferencing link" in Calendly UI is often custom_link in API
    if "custom" in k or "link" in k or "conference" in k:
        return "custom_link"
    return k


async def _get_calendly_location_kinds_for_event(token: str, event_type_uri: str) -> list[str]:
    """GET event type and return all configured location kinds (order preserved). Use exact values from API for POST /invitees."""
    if not event_type_uri or not token:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                event_type_uri,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status != 200:
                    body_preview = (await resp.text())[:500]
                    logger.warning(
                        "Calendly GET event type returned %s: %s. Set CALENDLY_LOCATION_KIND to fix booking (e.g. custom_link, zoom_conference).",
                        resp.status,
                        body_preview,
                    )
                    return []
                data = await resp.json()
        resource = data.get("resource") or data
        locations = resource.get("locations") or []
        if not locations and "location" in resource:
            locations = [resource["location"]]
        logger.info("Calendly event type locations (raw): %s", locations)
        kinds: list[str] = []
        for loc in locations:
            if not isinstance(loc, dict):
                kind = str(loc).strip() if loc else ""
            else:
                # API may use "kind", "type", or nested "location"; use as-is so we send what Calendly expects
                kind = (
                    loc.get("kind")
                    or loc.get("type")
                    or (loc.get("location") if isinstance(loc.get("location"), str) else "")
                    or ""
                ).strip()
            if kind and kind not in kinds:
                kinds.append(kind)
        if kinds:
            logger.info("Calendly location kinds from API: %s", kinds)
        else:
            logger.warning(
                "Calendly event type has no locations or could not parse them. Set CALENDLY_LOCATION_KIND (e.g. custom_link, zoom_conference)."
            )
        return kinds
    except Exception as e:
        logger.warning("Calendly get event type location failed: %s. Set CALENDLY_LOCATION_KIND to fix booking.", e)
        return []


async def _fetch_calendly_availability(days_ahead: int = 7) -> list[dict]:
    """Fetch available time slots from Calendly API. Returns list of {start, end} in ISO format."""
    token = (os.getenv("CALENDLY_ACCESS_TOKEN") or "").strip()
    if not token:
        logger.warning("Calendly not configured: set CALENDLY_ACCESS_TOKEN")
        return []
    event_type_uri = await _resolve_calendly_event_type_uri(token)
    if not event_type_uri:
        logger.warning("Calendly: set CALENDLY_EVENT_TYPE_URI or CALENDLY_SCHEDULING_LINK (e.g. https://calendly.com/you/event-slug)")
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
                    text = await resp.text()
                    logger.warning("Calendly API error %s: %s", resp.status, text[:200])
                    return []
                data = await resp.json()
    except Exception as e:
        logger.exception("Calendly request failed: %s", e)
        return []
    # Response shape: { "collection": [ { "start_time": "...", "end_time": "..." } ] } or nested under "resource"
    collection = data.get("collection") or data.get("items") or []
    slots = []
    for s in collection:
        r = s.get("resource") or s
        start = r.get("start_time")
        if start:
            slots.append({"start": start, "end": r.get("end_time")})
    return slots


@function_tool(
    description="Check calendar availability for the next few days. Call this after the user's booking details are stored, to offer them available time slots for the walkthrough. Returns a list of available start times you can read out to the user. Each time includes its ISO value in parentheses — when the user picks a time, use that exact ISO value in book_calendly_meeting."
)
async def check_calendly_availability(days_ahead: int = 7) -> str:
    """Fetch Calendly availability and return a short summary for the agent to speak."""
    slots = await _fetch_calendly_availability(days_ahead=days_ahead)
    if not slots:
        return "No availability could be loaded. You can ask the user to visit the booking link or try again later."
    # Format: human-readable time plus ISO in parentheses so agent can pass to book_calendly_meeting
    lines = []
    for s in slots[:20]:  # cap at 20 slots
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
    return "Available times: " + "; ".join(lines) + ". Offer these options to the user. When they choose one, call book_calendly_meeting with their email and the chosen start_time (use the ISO value in parentheses)."


async def _create_calendly_invitee(email: str, start_time_iso: str, invitee_timezone: str = "UTC") -> tuple[dict | None, str]:
    """Create a Calendly invitee (book the meeting). Returns (result, error_message). Calendly will send the confirmation email. Uses 'Guest' as invitee name."""
    token = (os.getenv("CALENDLY_ACCESS_TOKEN") or "").strip()
    if not token:
        logger.warning("Calendly create invitee skipped: CALENDLY_ACCESS_TOKEN not set")
        return None, "Calendly is not configured (missing token)."
    event_type_uri = await _resolve_calendly_event_type_uri(token)
    if not event_type_uri:
        logger.warning("Calendly create invitee skipped: could not resolve event type (set CALENDLY_EVENT_TYPE_URI or CALENDLY_SCHEDULING_LINK)")
        return None, "Calendly event type could not be resolved."
    # Prefer env when set: reliable override so booking works even if GET event type fails or returns unexpected shape
    raw_env = (os.getenv("CALENDLY_LOCATION_KIND") or "").strip() or None
    if raw_env:
        location_kinds_to_try = [_normalize_calendly_location_kind_from_env(raw_env)]
        logger.info("Calendly using location kind from CALENDLY_LOCATION_KIND: %r", location_kinds_to_try[0])
    else:
        location_kinds_to_try = await _get_calendly_location_kinds_for_event(token, event_type_uri)
        if not location_kinds_to_try:
            location_kinds_to_try = [_normalize_calendly_location_kind_from_env(raw_env)] if raw_env else []
        if not location_kinds_to_try:
            logger.warning("Calendly create invitee skipped: event type has no locations and CALENDLY_LOCATION_KIND not set")
            return None, "This event type has no location configured; set CALENDLY_LOCATION_KIND (e.g. custom_link, zoom_conference) to match your event type."
    # Normalize start_time to ISO with Z
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
        logger.info("Calendly create invitee request location.kind=%r", location_kind)
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
                    last_err_msg = f"Calendly API returned {resp.status}: {err_msg}"
                    logger.warning(
                        "Calendly create invitee failed: status=%s body=%s request_start_time=%s event_type=%s",
                        resp.status,
                        body,
                        start_time_iso,
                        event_type_uri,
                    )
                    # If 400 and error is about location kind, try next location
                    if resp.status == 400 and (
                        "location" in (err_msg or "").lower() or "invalid_location" in (err_msg or "").lower()
                    ):
                        continue
                    return None, last_err_msg
        except Exception as e:
            logger.exception("Calendly create invitee failed: %s", e)
            last_err_msg = str(e)
    return None, last_err_msg or "Booking could not be completed."


def _make_book_calendly_meeting_tool(room):
    """Factory so the tool can publish meeting_booked to the room and use per-room stored email."""

    @function_tool(
        description="Book the meeting in Calendly for the chosen time. Call this when the user has picked one of the available times. Use the exact start_time ISO string (e.g. 2026-03-14T14:00:00Z) from the availability list. Pass the user's email, or leave email empty to use the email stored for this call (from store_booking_details). Calendly will send them a confirmation email."
    )
    async def book_calendly_meeting(start_time: str, email: str = "") -> str:
        """Create Calendly invitee so the user gets a real confirmation email."""
        if not email or not email.strip():
            stored = _room_booking_store.get(room.name)
            if stored and stored.get("email"):
                email = stored["email"]
            else:
                return "No email was stored for this call. Ask the user for their email again, then call store_booking_details, then book the meeting."
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
            logger.info("Calendly meeting booked for %s at %s", email, start_time)
            return "The meeting is booked. Tell the user they will receive a confirmation email from Calendly shortly with the calendar invite and meeting link."
        return f"Booking could not be completed: {error_message}. Ask the user to try again or use the booking link on the website."

    return book_calendly_meeting


def prewarm(proc: JobProcess):
    logger.info("Worker prewarm complete")


async def entrypoint(ctx: JobContext):
    started_at = time.perf_counter()

    logger.info("Connecting to LiveKit room...")
    logger.info("Job room URL: %s", ctx._info.url)

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    logger.info("Connected to room: %s (%.2fs)", ctx.room.name, time.perf_counter() - started_at)

    room = ctx.room

    @function_tool(
        description="Call this ONLY after you have collected and confirmed the user's email, phone number, and country. Saves the booking details (email, phone, country only — no name) so the website and the agent can use them for the rest of the call. Do not call until all three fields are validated and confirmed."
    )
    async def store_booking_details(email: str, phone: str, country: str) -> str:
        email = (email or "").strip()
        logger.info("Captured email from voice agent: %s", email)
        if not email or "@" not in email or "." not in email or not EMAIL_REGEX.match(email):
            logger.warning("Email validation failed for: %s", email)
            return "The email format looks incorrect. Ask the user to repeat their email slowly."
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
        logger.info("Published booking_details to room: email=%s (stored for conversation)", email)
        return "Booking details have been saved. You can use get_stored_booking_details to recall them anytime this call, and when booking Calendly you can use the stored email. Thank the user and confirm their walkthrough request has been recorded."

    @function_tool(
        description="Get the booking details (email, phone, country) that were stored for this call. Call this when you need to recall the user's email, phone, or country — e.g. before offering times, when booking the meeting, or to confirm what you have on file."
    )
    async def get_stored_booking_details() -> str:
        """Return stored email, phone, country for the current room so the agent can use them in the conversation."""
        stored = _room_booking_store.get(room.name)
        if not stored:
            return "No booking details have been stored yet for this call. Collect email, phone, and country and call store_booking_details first."
        return f"Stored for this call: email {stored.get('email', '')}, phone {stored.get('phone', '')}, country {stored.get('country', '')}. Use this email when calling book_calendly_meeting."

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime-1.5",
            voice="marin",
            speed=0.9,
            turn_detection=ServerVad(
                type="server_vad",
                threshold=0.6,
                silence_duration_ms=200,
                prefix_padding_ms=150,
                create_response=True,
                interrupt_response=True,
            ),
        ),
    )

    agent = Agent(
        instructions=KNOWLEDGE_PROMPT,
        tools=[store_booking_details, get_stored_booking_details, check_calendly_availability, _make_book_calendly_meeting_tool(room)],
    )

    await session.start(agent=agent, room=ctx.room)

    logger.info("Agent session started (%.2fs)", time.perf_counter() - started_at)

    # Optional auto greeting; disable by default to avoid first-turn cold-start delay.
    auto_greeting = os.getenv("AGENT_AUTO_GREETING", "0").strip().lower() in {"1", "true", "yes"}
    if auto_greeting:
        await session.generate_reply(
            instructions="Say exactly once: Hey there! I'm Zia from Atlasium. How can I help you today?"
        )
        logger.info("Initial greeting generated (%.2fs)", time.perf_counter() - started_at)
    else:
        logger.info("Auto greeting disabled; waiting for user first turn")

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":

    logger.info("Starting LiveKit voice agent worker...")

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            job_executor_type=JobExecutorType.PROCESS,
            num_idle_processes=1,
        ),
    )