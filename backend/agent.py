import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

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

# Global VAD instance (loaded once)
_VAD_INSTANCE = None


def get_vad():
    """Get or create VAD instance (singleton)."""
    global _VAD_INSTANCE
    if _VAD_INSTANCE is None:
        logger.info("Loading VAD model...")
        _VAD_INSTANCE = silero.VAD.load()
        logger.info("VAD model loaded")
    return _VAD_INSTANCE


def _normalize_calendly_location_kind_from_env(raw: str) -> str:
    """Map env value to Calendly location kind."""
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


async def _fetch_calendly_availability() -> list[dict]:
    """Fetch available time slots from Calendly."""
    if not CALENDLY_TOKEN or not CALENDLY_EVENT_TYPE:
        return []

    now = datetime.now(timezone.utc)
    params = {
        "event_type": CALENDLY_EVENT_TYPE,
        "start_time": now.isoformat().replace("+00:00", "Z"),
        "end_time": (now + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{CALENDLY_API_BASE}/event_type_available_times",
                params=params,
                headers={"Authorization": f"Bearer {CALENDLY_TOKEN}"},
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

        collection = data.get("collection") or data.get("items") or []
        slots = []
        for s in collection[:10]:
            r = s.get("resource") or s
            start = r.get("start_time")
            if start:
                slots.append({"start": start})
        return slots
    except Exception as e:
        logger.error(f"Error fetching Calendly availability: {e}")
        return []


@function_tool(description="Check calendar availability.")
async def check_calendly_availability() -> str:
    """Return a human-readable list of available slots."""
    slots = await _fetch_calendly_availability()
    if not slots:
        return "No availability found. Ask user to visit booking link."

    lines = []
    for s in slots[:5]:
        start = s.get("start")
        if start:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                human = dt.strftime("%A %B %d at %I:%M %p UTC")
                lines.append(f"{human} ({start})")
            except Exception:
                lines.append(start)

    return "Available times: " + "; ".join(lines)


async def _create_calendly_invitee(email: str, start_time_iso: str) -> tuple[dict | None, str]:
    """
    Create a single-use booking link for the given Calendly event type.

    Note: this implementation generates a scheduling link. It does not guarantee
    the chosen slot is pre-filled, so the user confirms the exact time via
    the returned booking URL.
    """
    if not CALENDLY_TOKEN or not CALENDLY_EVENT_TYPE:
        return None, "Calendly not configured"

    start_time_iso = start_time_iso.strip().replace("+00:00", "Z")
    if not start_time_iso.endswith("Z"):
        start_time_iso += "Z"

    payload = {
        "max_event_count": 1,
        "owner": CALENDLY_EVENT_TYPE,
        "owner_type": "EventType",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CALENDLY_API_BASE}/scheduling_links",
                headers={
                    "Authorization": f"Bearer {CALENDLY_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as resp:
                if resp.status in (200, 201):
                    body = await resp.json()
                    link = body.get("resource", {}).get("booking_url", "")
                    if link:
                        logger.info(
                            "Created scheduling link for event type",
                        )
                        return ({"booking_url": link}, "")
                    return (body, "")

                error_text = await resp.text()
                logger.error(f"Calendly scheduling link error {resp.status}: {error_text}")
                return (
                    None,
                    f"API error {resp.status}: {error_text}",
                )
    except Exception as e:
        logger.error(f"Calendly scheduling link creation error: {e}")
        return None, str(e)


def _make_book_calendly_meeting_tool(room):
    """Factory that closes over the room for publishing results."""

    @function_tool(description="Book meeting in Calendly for chosen time.")
    async def book_calendly_meeting(start_time: str, email: str = "") -> str:
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
                    "booking_url": (result or {}).get("booking_url", ""),
                    "error": error if not result else None,
                }).encode("utf-8"),
                topic="meeting_booked",
            )
        )

        if result:
            booking_url = (result or {}).get("booking_url", "")
            if booking_url:
                return (
                    f"Booking link created: {booking_url}. "
                    "Use this link to confirm your slot."
                )
            return "Booking created! Use the calendar link we provided."
        return f"Booking failed: {error}. Want to try again?"

    return book_calendly_meeting


def prewarm(proc: JobProcess):
    """Pre-warm the worker process — load heavy models here."""
    logger.info("Pre-warming worker...")
    proc.userdata["vad"] = get_vad()
    proc.userdata["knowledge"] = KNOWLEDGE_PROMPT
    logger.info("Worker pre-warmed successfully")


async def _send_greeting(session: AgentSession, start_time: float):
    """
    Poll until the session is ready to speak, then greet immediately.
    Avoids a fixed sleep — fast infra gets ~instant greetings.
    """
    deadline = time.perf_counter() + 4.0
    interval = 0.05  # start at 50 ms, back off gently

    while time.perf_counter() < deadline:
        try:
            await session.generate_reply(
                instructions="Say: Hey there! I'm Zia from Atlasium. How can I help you today?"
            )
            logger.info(f"Greeting sent after {time.perf_counter() - start_time:.2f}s")
            return
        except Exception as e:
            logger.debug(f"Greeting not ready yet ({interval:.2f}s): {e}")
            await asyncio.sleep(interval)
            interval = min(interval * 1.5, 0.4)

    logger.error("Greeting failed — session never became ready")


async def entrypoint(ctx: JobContext):
    start_time = time.perf_counter()
    logger.info("Entrypoint started")

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info(f"Connected to room in {time.perf_counter() - start_time:.2f}s")

    room = ctx.room

    # ------------------------------------------------------------------ #
    # Tool definitions (closed over `room` for per-room booking storage)  #
    # ------------------------------------------------------------------ #

    @function_tool(description="Store email immediately after user confirms spelling.")
    async def store_email(email: str) -> str:
        if not EMAIL_REGEX.match(email.strip()):
            return "Invalid email format."

        existing = _room_booking_store.get(room.name, {})
        data = {
            "email": email.strip(),
            "phone": existing.get("phone", ""),
            "country": existing.get("country", ""),
        }
        _room_booking_store[room.name] = data

        asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps({"type": "booking_details", **data}).encode("utf-8"),
                topic=BOOKING_DATA_TOPIC,
            )
        )
        return "Email saved."

    @function_tool(description="Save booking contact details after collecting email, phone, country.")
    async def store_contact_details(email: str, country: str, phone: str = "") -> str:
        if not EMAIL_REGEX.match(email.strip()):
            return "Invalid email format."

        data = {
            "email": email.strip(),
            "phone": phone.strip(),
            "country": country.strip(),
        }
        _room_booking_store[room.name] = data

        asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps({"type": "booking_details", **data}).encode("utf-8"),
                topic=BOOKING_DATA_TOPIC,
            )
        )
        return "Booking details saved!"

    @function_tool(description="Get stored booking details.")
    async def get_stored_booking_details() -> str:
        stored = _room_booking_store.get(room.name)
        if not stored:
            return "No details stored yet."
        return f"Stored: email {stored['email']}"

    # ------------------------------------------------------------------ #
    # Session + Agent setup                                               #
    # ------------------------------------------------------------------ #

    vad_instance = ctx.proc.userdata.get("vad") or get_vad()

    logger.info("Creating agent session...")
    session = AgentSession(
        vad=vad_instance,
        llm=openai.realtime.RealtimeModel(
            model="gpt-4o-realtime-preview-2024-12-17",
            voice="shimmer",
            temperature=2.0,
            turn_detection={
                "type": "server_vad",
                "threshold": 0.9,
                "silence_duration_ms": 500,
                "prefix_padding_ms": 300,
            },
        ),
    )

    book_calendly_meeting = _make_book_calendly_meeting_tool(room)

    agent = Agent(
        instructions=KNOWLEDGE_PROMPT,
        tools=[
            store_email,
            store_contact_details,
            get_stored_booking_details,
            check_calendly_availability,
            book_calendly_meeting,
        ],
    )

    session_start = time.perf_counter()
    await session.start(agent=agent, room=room)
    logger.info(f"Session started in {time.perf_counter() - session_start:.2f}s")
#---------------------------------------------------------------------------------------------------
    # ------------------------------------------------------------------ #
    # Console transcript logs                                              #
    # ------------------------------------------------------------------ #
    def _extract_text_content(content) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    s = item.strip()
                    if s:
                        parts.append(s)
                    continue
                text_val = getattr(item, "text", None)
                if isinstance(text_val, str):
                    s = text_val.strip()
                    if s:
                        parts.append(s)
            return " ".join(parts).strip()
        return ""

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(ev):
        if getattr(ev, "is_final", False) and getattr(ev, "transcript", "").strip():
            logger.info(f"[USER] {ev.transcript.strip()}")

    @session.on("conversation_item_added")
    def _on_conversation_item_added(ev):
        item = getattr(ev, "item", None)
        role = getattr(item, "role", "")
        if role != "assistant":
            return

        text = _extract_text_content(getattr(item, "content", ""))
        if text:
            logger.info(f"[AI] {text}")
#--------------------------------------------------------------------------------------------------
    # ------------------------------------------------------------------ #
    # Fire greeting immediately after session starts — don't wait for   #
    # the participant to connect. The greeting will buffer and play     #
    # once the participant subscribes to the agent audio track.         #
    # ------------------------------------------------------------------ #

    asyncio.create_task(_send_greeting(session, start_time))

    # ------------------------------------------------------------------ #
    # Keep alive until the room disconnects                               #
    # ------------------------------------------------------------------ #

    disconnected = asyncio.Event()
    room.on("disconnected", lambda: disconnected.set())

    await disconnected.wait()
    logger.info("Room disconnected — agent exiting")

    # Clean up per-room booking data
    _room_booking_store.pop(room.name, None)


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