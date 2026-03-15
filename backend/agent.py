import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

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
from livekit.agents.worker import JobExecutorType
from livekit.plugins import openai, silero

# Topic for booking details data messages
BOOKING_DATA_TOPIC = "booking_details"

# Per-room storage
_room_booking_store: dict[str, dict[str, str]] = {}
_availability_cache: dict[str, dict[str, object]] = {}

# Email format validation
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Calendly API
CALENDLY_API_BASE = "https://api.calendly.com"

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-assistant")

# Load knowledge base
KNOWLEDGE_FILE = Path(__file__).parent / "knowledge.txt"
KNOWLEDGE_PROMPT = KNOWLEDGE_FILE.read_text(encoding="utf-8")

# Force threaded DNS resolver
if os.getenv("LIVEKIT_FORCE_THREADED_DNS", "1").strip().lower() in {"1", "true", "yes"}:
    aiohttp_resolver.DefaultResolver = aiohttp_resolver.ThreadedResolver
    aiohttp_connector.DefaultResolver = aiohttp_resolver.ThreadedResolver
    logger.info("Using threaded DNS resolver")

# Cache Calendly config at module level
CALENDLY_TOKEN = (os.getenv("CALENDLY_ACCESS_TOKEN") or "").strip()
CALENDLY_EVENT_TYPE = (os.getenv("CALENDLY_EVENT_TYPE_URI") or "").strip()
CALENDLY_LOCATION_KIND = (os.getenv("CALENDLY_LOCATION_KIND") or "").strip()
CALENDLY_SCHEDULING_LINK = (os.getenv("CALENDLY_SCHEDULING_LINK") or "").strip()
BOOKING_FALLBACK_WEBHOOK_URL = (
    os.getenv("BOOKING_FALLBACK_WEBHOOK_URL")
    or "https://atlasium788ai.app.n8n.cloud/webhook/voice-agent-book-meeting-fallback"
).strip()

logger.info(
    "Calendly config — token_set=%s event_type=%s location_kind=%s",
    bool(CALENDLY_TOKEN),
    CALENDLY_EVENT_TYPE or "NOT SET",
    CALENDLY_LOCATION_KIND or "NOT SET",
)

# Global VAD instance (loaded once)
_VAD_INSTANCE = None


def get_vad():
    global _VAD_INSTANCE
    if _VAD_INSTANCE is None:
        logger.info("Loading VAD model...")
        _VAD_INSTANCE = silero.VAD.load()
        logger.info("VAD model loaded")
    return _VAD_INSTANCE


def _normalize_calendly_location_kind_from_env(raw: str) -> str:
    if not raw:
        return "custom_link"
    k = raw.strip().lower()
    if k in ("custom", "custom_link", "zoom_conference", "google_meet"):
        return k
    if "zoom" in k:
        return "zoom_conference"
    if "google" in k or "meet" in k:
        return "google_meet"
    return "custom_link"


def _format_slots_for_speech(slots: list[dict], limit: int = 3) -> str:
    lines: list[str] = []
    for s in slots[:limit]:
        start = s.get("start")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            lines.append(f"{dt.strftime('%A %B %d at %I:%M %p UTC')} ({start})")
        except Exception:
            lines.append(str(start))
    return "; ".join(lines)


async def _send_fallback_webhook(email: str, reason: str, extra: dict | None = None) -> str:
    if not BOOKING_FALLBACK_WEBHOOK_URL:
        return "Fallback webhook URL is not configured"
    payload = {
        "email": email.strip(),
        "reason": reason,
        "calendlySchedulingLink": CALENDLY_SCHEDULING_LINK,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                BOOKING_FALLBACK_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as resp:
                if resp.status in (200, 201, 202):
                    return ""
                text = await resp.text()
                return f"Fallback webhook error {resp.status}: {text[:120]}"
    except Exception as e:
        return str(e)


async def _fetch_calendly_availability() -> list[dict]:
    if not CALENDLY_TOKEN or not CALENDLY_EVENT_TYPE:
        logger.error("Calendly not configured — token_set=%s event_type_set=%s", bool(CALENDLY_TOKEN), bool(CALENDLY_EVENT_TYPE))
        return []

    base_start = datetime.now(timezone.utc) + timedelta(hours=1)

    def _build_window(start_dt: datetime) -> dict[str, str]:
        return {
            "event_type": CALENDLY_EVENT_TYPE,
            "start_time": start_dt.isoformat().replace("+00:00", "Z"),
            "end_time": (start_dt + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        }

    async def _fetch_window(session: aiohttp.ClientSession, start_dt: datetime) -> list[dict]:
        params = _build_window(start_dt)
        logger.info("Fetching Calendly window start=%s", params["start_time"])
        async with session.get(
            f"{CALENDLY_API_BASE}/event_type_available_times",
            params=params,
            headers={"Authorization": f"Bearer {CALENDLY_TOKEN}"},
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error("Calendly availability error status=%s body=%s", resp.status, body[:300])
                if resp.status == 400 and "start_time must be in the future" in body:
                    retry_start = datetime.now(timezone.utc) + timedelta(minutes=15)
                    retry_params = _build_window(retry_start)
                    async with session.get(
                        f"{CALENDLY_API_BASE}/event_type_available_times",
                        params=retry_params,
                        headers={"Authorization": f"Bearer {CALENDLY_TOKEN}"},
                    ) as retry_resp:
                        if retry_resp.status != 200:
                            retry_body = await retry_resp.text()
                            logger.error("Calendly retry error status=%s body=%s", retry_resp.status, retry_body[:300])
                            return []
                        data = await retry_resp.json()
                else:
                    return []
            else:
                data = await resp.json()

        collection = data.get("collection") or data.get("items") or []
        window_slots: list[dict] = []
        for s in collection:
            start = s.get("start_time")
            if start:
                window_slots.append({"start": start})
        logger.info("Calendly window slots found=%s", len(window_slots))
        return window_slots

    try:
        async with aiohttp.ClientSession() as session:
            first_week = await _fetch_window(session, base_start)
            second_week = await _fetch_window(session, base_start + timedelta(days=7))
        merged = first_week + second_week
        seen: set[str] = set()
        deduped: list[dict] = []
        for slot in merged:
            start = slot.get("start")
            if not isinstance(start, str) or start in seen:
                continue
            seen.add(start)
            deduped.append({"start": start})
        logger.info("Calendly total slots after merge=%s", len(deduped))
        return deduped[:30]
    except Exception as e:
        logger.error("Error fetching Calendly availability: %s", e)
        return []


async def _get_next_slot_batch(email: str, reset: bool = False, batch_size: int = 3) -> list[dict]:
    key = email.strip().lower()
    if not key:
        return []

    cache = _availability_cache.get(key)
    if reset or cache is None:
        slots = await _fetch_calendly_availability()
        if not slots:
            logger.error(
                "Calendly: _fetch_calendly_availability returned 0 slots. "
                "Check token, event_type_uri, and that the event type has future availability in Calendly dashboard."
            )
        cache = {"slots": slots, "cursor": 0}
        _availability_cache[key] = cache

    slots = cache.get("slots")
    cursor = cache.get("cursor", 0)
    if not isinstance(slots, list):
        slots = []
    if not isinstance(cursor, int):
        cursor = 0

    if cursor >= len(slots):
        return []

    next_cursor = min(cursor + batch_size, len(slots))
    batch = slots[cursor:next_cursor]
    cache["cursor"] = next_cursor
    return batch


def _test_calendly_on_startup() -> None:
    token_preview = (CALENDLY_TOKEN[:20] + "...") if CALENDLY_TOKEN else "<missing>"
    logger.info(
        "Calendly startup test — token=%s event_type=%s location_kind=%s",
        token_preview,
        CALENDLY_EVENT_TYPE or "<missing>",
        CALENDLY_LOCATION_KIND or "<missing>",
    )
    if not CALENDLY_TOKEN or not CALENDLY_EVENT_TYPE:
        logger.error("Calendly startup test skipped — missing token or event_type")
        return

    now_utc = datetime.now(timezone.utc)
    start_time = (now_utc + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    end_time = (now_utc + timedelta(days=8)).isoformat().replace("+00:00", "Z")
    params = urlencode({
        "event_type": CALENDLY_EVENT_TYPE,
        "start_time": start_time,
        "end_time": end_time,
    })
    url = f"{CALENDLY_API_BASE}/event_type_available_times?{params}"
    req = Request(
        url,
        headers={"Authorization": f"Bearer {CALENDLY_TOKEN}", "Content-Type": "application/json"},
        method="GET",
    )
    status = 0
    body_text = ""
    try:
        with urlopen(req, timeout=20) as resp:
            status = resp.getcode()
            body_text = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        status = e.code
        body_text = e.read().decode("utf-8", errors="replace")
    except URLError as e:
        logger.error("Calendly startup test request error: %s", e)
        return
    except Exception as e:
        logger.error("Calendly startup test unexpected error: %s", e)
        return

    if status != 200:
        logger.error("Calendly startup test FAILED status=%s body=%s", status, body_text[:500])
    else:
        logger.info("Calendly startup test OK status=%s body=%s", status, body_text[:500])

    try:
        parsed = json.loads(body_text) if body_text else {}
        collection = parsed.get("collection") if isinstance(parsed, dict) else None
        count = len(collection) if isinstance(collection, list) else 0
        logger.info("Calendly startup test collection_count=%s", count)
    except Exception as e:
        logger.error("Calendly startup test parse error: %s", e)


@function_tool(description="Check calendar availability. Call this after storing booking details to offer the user available time slots.")
async def check_calendly_availability(email: str = "") -> str:
    logger.info("Tool called: check_calendly_availability email=%s", email)
    clean_email = email.strip()
    if not clean_email:
        stored = _room_booking_store.get("active_room", {})
        clean_email = str(stored.get("email", "")).strip()

    slots = await _get_next_slot_batch(clean_email, reset=False, batch_size=3)
    logger.info("check_calendly_availability slots_returned=%s", len(slots))

    if not slots:
        if clean_email:
            webhook_error = await _send_fallback_webhook(
                clean_email,
                reason="no_more_slots_available",
                extra={"flow": "check_calendly_availability"},
            )
            if webhook_error:
                logger.warning("Fallback webhook failed: %s", webhook_error)
        if CALENDLY_SCHEDULING_LINK:
            return (
                f"No availability found right now. Share this booking link with the user: {CALENDLY_SCHEDULING_LINK}. "
                "Continue the call and offer to try another time."
            )
        return "No availability found right now. Continue call and ask user for another preferred time."

    return (
        "Next available times: "
        + _format_slots_for_speech(slots)
        + ". If user is not okay with these, call check_calendly_availability again for more slots."
    )


async def _create_calendly_invitee(email: str, start_time_iso: str) -> tuple[dict | None, str]:
    if not CALENDLY_TOKEN or not CALENDLY_EVENT_TYPE:
        return None, "Calendly not configured"
    location_kind = _normalize_calendly_location_kind_from_env(CALENDLY_LOCATION_KIND)
    start_time_iso = start_time_iso.strip().replace("+00:00", "Z")
    if not start_time_iso.endswith("Z"):
        start_time_iso += "Z"
    payload = {
        "event_type": CALENDLY_EVENT_TYPE,
        "start_time": start_time_iso,
        "invitee": {
            "email": email.strip(),
            "name": "Guest",
            "timezone": "UTC",
        },
        "location": {"kind": location_kind},
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CALENDLY_API_BASE}/invitees",
                headers={"Authorization": f"Bearer {CALENDLY_TOKEN}", "Content-Type": "application/json"},
                json=payload,
            ) as resp:
                if resp.status in (200, 201):
                    body = await resp.json()
                    resource = body.get("resource") if isinstance(body, dict) else {}
                    if not isinstance(resource, dict):
                        resource = {}
                    return ({"status": resource.get("status") or "ACTIVE", "raw": body}, "")
                error_text = await resp.text()
                logger.error("Calendly API error %s: %s", resp.status, error_text[:200])
                return None, f"API error {resp.status}: {error_text[:160]}"
    except Exception as e:
        logger.error("Calendly invitee creation error: %s", e)
        return None, str(e)


def _make_book_calendly_meeting_tool(room):
    @function_tool(
        description=(
            "Book meeting in Calendly for a selected start_time and email. "
            "If email is omitted, use stored email from prior details collection."
        )
    )
    async def book_calendly_meeting(start_time: str, email: str = "") -> str:
        logger.info("Tool called: book_calendly_meeting start_time=%s email=%s", start_time, email)
        if not email:
            stored = _room_booking_store.get(room.name, {})
            email = stored.get("email", "")
            if not email:
                return "No email stored. Ask user for email first."

        result, error = await _create_calendly_invitee(email, start_time)

        asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps({
                    "type": "meeting_booked",
                    "booked": result is not None,
                    "start_time": start_time,
                    "email": email,
                    "error": error if not result else None,
                    "result": result if result else None,
                }).encode("utf-8"),
                topic="meeting_booked",
            )
        )

        if result:
            logger.info("Calendly meeting booked for %s at %s", email, start_time)
            return "Meeting booked! User will receive Calendly confirmation email."

        fallback_error = await _send_fallback_webhook(
            email,
            reason="calendly_booking_failed",
            extra={"requestedStartTime": start_time, "bookingError": error},
        )
        if fallback_error:
            logger.warning("Fallback webhook failed: %s", fallback_error)
            return f"Booking failed: {error}. Also failed to notify fallback webhook: {fallback_error}"
        if CALENDLY_SCHEDULING_LINK:
            return (
                f"Booking failed: {error}. "
                f"I have sent fallback details and you can share this Calendly link: {CALENDLY_SCHEDULING_LINK}"
            )
        return f"Booking failed: {error}"

    return book_calendly_meeting


def prewarm(proc: JobProcess):
    logger.info("Pre-warming worker...")
    _test_calendly_on_startup()
    proc.userdata["vad"] = get_vad()
    proc.userdata["knowledge"] = KNOWLEDGE_PROMPT
    logger.info("Worker pre-warmed successfully")


async def entrypoint(ctx: JobContext):
    start_time = time.perf_counter()
    logger.info("Entrypoint started")

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info("Connected to room in %.2fs", time.perf_counter() - start_time)

    room = ctx.room

    @function_tool(
        description=(
            "Save booking details. Email is required. Phone and country are optional. "
            "After saving, immediately returns available Calendly slots."
        )
    )
    async def store_booking_details(email: str, phone: str = "", country: str = "") -> str:
        logger.info("Tool called: store_booking_details email=%s phone=%s country=%s", email, phone, country)
        if not EMAIL_REGEX.match(email.strip()):
            return "Invalid email format. Ask the user to repeat their email slowly."

        data = {"email": email.strip(), "phone": phone.strip(), "country": country.strip()}
        _room_booking_store[room.name] = data
        _room_booking_store["active_room"] = data

        asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps({"type": "booking_details", **data}).encode("utf-8"),
                topic=BOOKING_DATA_TOPIC,
            )
        )

        slots = await _get_next_slot_batch(data["email"], reset=True, batch_size=3)
        logger.info("store_booking_details slots_returned=%s", len(slots))

        if slots:
            return (
                "Booking details saved. Available times are: "
                + _format_slots_for_speech(slots)
                + ". Ask user to pick one. If user rejects these, call check_calendly_availability to get more slots."
            )

        webhook_error = await _send_fallback_webhook(
            data["email"],
            reason="no_slots_after_email_collection",
            extra={"flow": "store_booking_details"},
        )
        if webhook_error:
            logger.warning("Fallback webhook failed: %s", webhook_error)
        if CALENDLY_SCHEDULING_LINK:
            return (
                "Booking details saved, but no slots are available right now. "
                f"Use this Calendly link as fallback: {CALENDLY_SCHEDULING_LINK}"
            )
        return "Booking details saved, but no slots are available right now."

    @function_tool(description="Get stored booking details for this call.")
    async def get_stored_booking_details() -> str:
        stored = _room_booking_store.get(room.name)
        if not stored:
            return "No details stored yet."
        return f"Stored: email {stored['email']}, phone {stored.get('phone', '')}, country {stored.get('country', '')}"

    vad_instance = ctx.proc.userdata.get("vad") or get_vad()

    logger.info("Creating agent session...")
    session = AgentSession(
        vad=vad_instance,
        llm=openai.realtime.RealtimeModel(
            model="gpt-4o-realtime-preview-2024-12-17",
            voice="alloy",
            temperature=0.8,
            turn_detection={
                "type": "server_vad",
                "threshold": 0.7,
                "silence_duration_ms": 600,
                "prefix_padding_ms": 300,
            },
        ),
    )

    agent = Agent(
        instructions=KNOWLEDGE_PROMPT,
        tools=[
            store_booking_details,
            get_stored_booking_details,
            check_calendly_availability,
            _make_book_calendly_meeting_tool(room),
        ],
    )

    session_start = time.perf_counter()
    await session.start(agent=agent, room=room)
    logger.info("Session started in %.2fs", time.perf_counter() - session_start)

    try:
        await session.generate_reply(
            instructions="Say: Hey there! I'm Zia from Atlasium. How can I help you today?"
        )
        logger.info("Greeting sent in %.2fs", time.perf_counter() - start_time)
    except Exception as e:
        logger.error("Failed to send greeting: %s", e)
        try:
            await asyncio.sleep(0.2)
            await session.generate_reply(
                instructions="Say: Hi! I'm Zia. How can I help you today?"
            )
        except Exception as e2:
            logger.error("Retry failed: %s", e2)

    while True:
        await asyncio.sleep(5)


if __name__ == "__main__":
    logger.info("Starting voice agent...")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            job_executor_type=JobExecutorType.PROCESS,
            num_idle_processes=2,
        ),
    )