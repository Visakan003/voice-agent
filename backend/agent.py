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
CALENDLY_FALLBACK_WEBHOOK_URL = (
    os.getenv("CALENDLY_FALLBACK_WEBHOOK_URL")
    or "https://atlasium788ai.app.n8n.cloud/webhook/calendy-send-fallback-mail"
).strip()

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


async def _trigger_calendly_fallback_mail(
    *,
    email: str = "",
    phone: str = "",
    meeting_book_link: str = "",
    reason: str = "",
) -> None:
    """Send fallback payload to n8n webhook when Calendly API fails."""
    if not CALENDLY_FALLBACK_WEBHOOK_URL:
        logger.warning("Calendly fallback webhook URL missing; skipping fallback notification")
        return

    payload = {
        "email": (email or "").strip(),
        "phone": (phone or "").strip(),
        "meeting_book_link": (meeting_book_link or "").strip(),
        "reason": (reason or "").strip(),
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                CALENDLY_FALLBACK_WEBHOOK_URL,
                headers={"Content-Type": "application/json"},
                json=payload,
            ) as resp:
                body = await resp.text()
                if resp.status in (200, 201, 202):
                    logger.info(
                        f"Calendly fallback webhook sent successfully status={resp.status} payload={payload}"
                    )
                else:
                    logger.error(
                        f"Calendly fallback webhook failed status={resp.status} body={body} payload={payload}"
                    )
    except Exception as e:
        logger.error(f"Calendly fallback webhook error: {e} payload={payload}")


async def _fetch_calendly_availability() -> tuple[list[dict], str | None]:
    """Fetch available time slots from Calendly."""
    if not CALENDLY_TOKEN or not CALENDLY_EVENT_TYPE:
        return [], "Calendly not configured"

    now = datetime.now(timezone.utc) + timedelta(hours=1)
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
                    err = await resp.text()
                    return [], f"API error {resp.status}: {err}"
                data = await resp.json()

        collection = data.get("collection") or data.get("items") or []
        slots = []
        for s in collection[:10]:
            start = s.get("start_time")
            if start:
                slots.append({"start": start})
        return slots, None
    except Exception as e:
        logger.error(f"Error fetching Calendly availability: {e}")
        return [], str(e)


@function_tool(description="Check calendar availability.")
async def check_calendly_availability() -> str:
    """Return a human-readable list of available slots."""
    slots, error = await _fetch_calendly_availability()
    if error:
        return f"No availability found. Calendly error: {error}"
    if not slots:
        return "No availability found. Ask user to visit booking link."

    lines = []
    for s in slots[:5]:
        start = s.get("start")
        if start:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                time_part = dt.strftime("%I:%M %p").lstrip("0")
                human = dt.strftime("%A %B %d") + f" at {time_part} UTC"
                lines.append(f"{human} ({start})")
            except Exception:
                lines.append(start)

    return "Available times: " + "; ".join(lines)


async def _create_calendly_invitee(email: str, start_time_iso: str) -> tuple[dict | None, str]:
    """Create a confirmed Calendly booking for the selected slot."""
    if not CALENDLY_TOKEN or not CALENDLY_EVENT_TYPE:
        return None, "Calendly not configured"

    start_time_iso = start_time_iso.strip().replace("+00:00", "Z")
    if not start_time_iso.endswith("Z"):
        start_time_iso += "Z"

    location_kind = _normalize_calendly_location_kind_from_env(CALENDLY_LOCATION_KIND)

    logger.info("Calendly: creating invitee booking")
    logger.info(
        f"Calendly: event_type={CALENDLY_EVENT_TYPE} start_time={start_time_iso} email_present={bool(email)}"
    )

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
            # Preferred path for direct booking: /invitees
            async with session.post(
                f"{CALENDLY_API_BASE}/invitees",
                headers={
                    "Authorization": f"Bearer {CALENDLY_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as resp:
                if resp.status in (200, 201):
                    body = await resp.json()
                    resource = body.get("resource", {})
                    scheduled_event_uri = resource.get("scheduled_event", "")
                    invitee_uri = resource.get("uri", "")
                    logger.info(
                        f"Calendly: invitee created scheduled_event_uri={scheduled_event_uri} invitee_uri={invitee_uri}"
                    )
                    return (
                        {
                            "scheduled_event_uri": scheduled_event_uri,
                            "invitee_uri": invitee_uri,
                        },
                        "",
                    )

                error_text = await resp.text()
                logger.error(f"Calendly invitee booking error {resp.status}: {error_text}")

                # Optional fallback for environments that only expose /scheduled_events.
                fallback_payload = {
                    "event_type": CALENDLY_EVENT_TYPE,
                    "start_time": start_time_iso,
                    "invitee": {
                        "email": email.strip(),
                        "name": "Guest",
                        "timezone": "UTC",
                    },
                }
                async with session.post(
                    f"{CALENDLY_API_BASE}/scheduled_events",
                    headers={
                        "Authorization": f"Bearer {CALENDLY_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    json=fallback_payload,
                ) as fallback_resp:
                    if fallback_resp.status in (200, 201):
                        body = await fallback_resp.json()
                        scheduled_event_uri = body.get("resource", {}).get("uri", "")
                        logger.info(
                            f"Calendly: scheduled event created scheduled_event_uri={scheduled_event_uri}"
                        )
                        return ({"scheduled_event_uri": scheduled_event_uri}, "")

                    fallback_error_text = await fallback_resp.text()
                    logger.error(
                        f"Calendly scheduled_events booking error {fallback_resp.status}: {fallback_error_text}"
                    )
                    return (
                        None,
                        f"API error invitees={resp.status}: {error_text}; scheduled_events={fallback_resp.status}: {fallback_error_text}",
                    )
    except Exception as e:
        logger.error(f"Calendly booking creation error: {e}")
        return None, str(e)


def _make_book_calendly_meeting_tool(room):
    """Factory that closes over the room for publishing results."""

    @function_tool(description="Book meeting in Calendly for chosen time.")
    async def book_calendly_meeting(start_time: str, email: str = "") -> str:
        stored = _room_booking_store.get(room.name, {})
        if stored.get("bookingDeclined") == "1":
            return "User declined booking. Do not book."

        if not email:
            email = stored.get("email", "")
            if not email:
                return "No email stored. Ask user for email first."

        result, error = await _create_calendly_invitee(email, start_time)

        logger.info("Calendly: booking result", extra={})
        if result:
            logger.info("Calendly: booked event", extra={})
            logger.info(
                f"Calendly: scheduled_event_uri={(result or {}).get('scheduled_event_uri','')} invitee_uri={(result or {}).get('invitee_uri','')}"
            )
        else:
            logger.info(f"Calendly: error={error}")

        asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps({
                    "type": "meeting_booked",
                    "booked": result is not None,
                    "start_time": start_time,
                    "email": email,
                    "scheduled_event_uri": (result or {}).get("scheduled_event_uri", ""),
                    "invitee_uri": (result or {}).get("invitee_uri", ""),
                    "error": error if not result else None,
                }).encode("utf-8"),
                topic="meeting_booked",
            )
        )

        if result:
            return (
                "Perfect - your walkthrough is booked and Calendly has confirmed it. "
                "You'll receive a confirmation email shortly."
            )

        # Trigger fallback mail webhook on booking errors.
        stored = _room_booking_store.get(room.name, {})
        asyncio.create_task(
            _trigger_calendly_fallback_mail(
                email=email or stored.get("email", ""),
                phone=stored.get("phone", ""),
                meeting_book_link=CALENDLY_SCHEDULING_LINK,
                reason=f"booking_failed: {error}",
            )
        )
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

    def _extract_payload_bytes(packet: object) -> bytes:
        if isinstance(packet, (bytes, bytearray)):
            return bytes(packet)
        data_attr = getattr(packet, "data", None)
        if isinstance(data_attr, (bytes, bytearray)):
            return bytes(data_attr)
        payload_attr = getattr(packet, "payload", None)
        if isinstance(payload_attr, (bytes, bytearray)):
            return bytes(payload_attr)
        return b""

    def _extract_topic(packet: object) -> str:
        topic_attr = getattr(packet, "topic", None)
        if isinstance(topic_attr, str):
            return topic_attr
        return ""

    async def _handle_prefill_contact(packet: object) -> None:
        topic = _extract_topic(packet)
        if topic and topic != "prefill_contact":
            return
        payload = _extract_payload_bytes(packet)
        if not payload:
            return
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        if data.get("type") not in (None, "prefill_contact"):
            return

        existing = _room_booking_store.get(room.name, {})
        updated = {
            "email": str(data.get("email", existing.get("email", ""))).strip(),
            "phone": str(data.get("phone", existing.get("phone", ""))).strip(),
            "country": str(data.get("country", existing.get("country", ""))).strip(),
            "countryCode": str(data.get("countryCode", existing.get("countryCode", ""))).strip(),
            "formShown": "0",
            "formSubmitted": "1",
        }
        _room_booking_store[room.name] = updated

        await room.local_participant.publish_data(
            json.dumps({"type": "prefill_confirmed", "stored": True}).encode("utf-8"),
            topic="prefill_confirmed",
        )

    def _on_data_received(packet: object, *_args) -> None:
        asyncio.create_task(_handle_prefill_contact(packet))

    try:
        room.on("data_received", _on_data_received)
    except Exception:
        pass

    # ------------------------------------------------------------------ #
    # Tool definitions (closed over `room` for per-room booking storage)  #
    # ------------------------------------------------------------------ #

    @function_tool(description="Get stored booking details.")
    async def get_stored_booking_details() -> str:
        stored = _room_booking_store.get(room.name)
        if not stored:
            return "No details stored yet."
        email = str(stored.get("email", "")).strip()
        if not email:
            return "No details stored yet."
        return f"Stored: email {email}"

    @function_tool(
        description=(
            "Show popup booking form to collect email, phone and country code. "
            "Use this first when booking is requested and no email is stored."
        )
    )
    async def show_booking_form() -> str:
        stored = _room_booking_store.get(room.name, {})
        if stored.get("formShown") == "1":
            return "Booking form is already shown. Wait for user to submit."
        _room_booking_store[room.name] = {
            **stored,
            "formShown": "1",
            "formSubmitted": "0",
            "bookingDeclined": "0",
        }
        await room.local_participant.publish_data(
            json.dumps({"type": "show_booking_form"}).encode("utf-8"),
            topic="show_booking_form",
        )
        return "Booking form shown. Wait for submission; do not ask contact details verbally."

    @function_tool(
        description=(
            "Close popup booking form and stop booking flow when user declines "
            "to book, says no demo, or refuses to fill details."
        )
    )
    async def close_booking_form() -> str:
        stored = _room_booking_store.get(room.name, {})
        _room_booking_store[room.name] = {
            **stored,
            "formShown": "0",
            "formSubmitted": "0",
            "bookingDeclined": "1",
        }
        await room.local_participant.publish_data(
            json.dumps({"type": "close_booking_form"}).encode("utf-8"),
            topic="close_booking_form",
        )
        return "Booking form closed and booking flow cancelled. Continue normal conversation."

    @function_tool(description="Check calendar availability.")
    async def check_room_calendly_availability() -> str:
        stored = _room_booking_store.get(room.name, {})
        # Guard: never continue booking while the form is pending.
        if stored.get("formShown") == "1" and not str(stored.get("email", "")).strip():
            return "Booking form is still pending. Wait for user to submit or cancel booking."
        # Guard: if user declined booking, do not proceed.
        if stored.get("bookingDeclined") == "1":
            return "User declined booking. Do not continue booking unless they ask again."

        slots, error = await _fetch_calendly_availability()
        if error:
            asyncio.create_task(
                _trigger_calendly_fallback_mail(
                    email=stored.get("email", ""),
                    phone=stored.get("phone", ""),
                    meeting_book_link=CALENDLY_SCHEDULING_LINK,
                    reason=f"availability_failed: {error}",
                )
            )
            return "No availability found. Ask user to visit booking link."

        if not slots:
            return "No availability found. Ask user to visit booking link."

        lines = []
        for s in slots[:5]:
            start = s.get("start")
            if start:
                try:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    time_part = dt.strftime("%I:%M %p").lstrip("0")
                    human = dt.strftime("%A %B %d") + f" at {time_part} UTC"
                    lines.append(f"{human} ({start})")
                except Exception:
                    lines.append(start)
        return "Available times: " + "; ".join(lines)

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
            temperature=0.8,                    # FIX: was 0.8 — lower = less creative guessing
            input_audio_transcription={
                "model": "whisper-1",
                "prompt": (                     # FIX: bias STT toward individual letters
                    "The user is spelling out an email address one character at a time. "
                    "Each spoken segment is a SINGLE letter or symbol. "
                    "Common patterns: 'at' means @, 'dot' means ., 'underscore' means _. "
                    "Do NOT merge letters into words. "
                    "Example: P R E M N A T H = 'p r e m n a t h', not 'premnath'. "
                    "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo Lima "
                    "Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey "
                    "X-ray Yankee Zulu are NATO phonetics for single letters."
                ),
            },
            turn_detection={
                "type": "server_vad",
                "threshold": 0.9,
                "silence_duration_ms": 1500,    # FIX: was 1000 — more pause tolerance for spelling
                "prefix_padding_ms": 300,
            },
        ),
    )

    book_calendly_meeting = _make_book_calendly_meeting_tool(room)

    agent = Agent(
        instructions=KNOWLEDGE_PROMPT,
        tools=[
            get_stored_booking_details,
            show_booking_form,
            close_booking_form,
            check_room_calendly_availability,
            book_calendly_meeting,
        ],
    )

    session_start = time.perf_counter()
    await session.start(agent=agent, room=room)
    logger.info(f"Session started in {time.perf_counter() - session_start:.2f}s")

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

    # ------------------------------------------------------------------ #
    # Fire greeting immediately after session starts                      #
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