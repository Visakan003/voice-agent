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
    llm
)
from livekit.agents import worker as livekit_worker
from livekit.agents.worker import JobExecutorType
from livekit.plugins import openai, silero
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

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


def _normalize_calendly_location_kind_from_env(raw: str) -> str:
    """Map env value to kind."""
    if not raw:
        return "custom_link"  # Default
    k = raw.strip().lower()
    if k in ("custom", "custom_link", "zoom_conference", "google_meet"):
        return k
    if "zoom" in k:
        return "zoom_conference"
    if "google" in k or "meet" in k:
        return "google_meet"
    return "custom_link"


async def _fetch_calendly_availability() -> list[dict]:
    """Fetch available time slots - simplified."""
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
                headers={"Authorization": f"Bearer {CALENDLY_TOKEN}"}
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
    except Exception:
        return []


@function_tool(
    description="Check calendar availability."
)
async def check_calendly_availability() -> str:
    """Fast availability check."""
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
    """Create invitee - simplified."""
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
        "location": {"kind": location_kind}
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CALENDLY_API_BASE}/invitees",
                headers={"Authorization": f"Bearer {CALENDLY_TOKEN}", "Content-Type": "application/json"},
                json=payload,
            ) as resp:
                if resp.status in (200, 201):
                    return ({}, "")
                return None, f"API error {resp.status}"
    except Exception as e:
        return None, str(e)


def _make_book_calendly_meeting_tool(room):
    """Factory for booking tool."""
    
    @function_tool(
        description="Book meeting in Calendly for chosen time."
    )
    async def book_calendly_meeting(start_time: str, email: str = "") -> str:
        """Create Calendly invitee."""
        if not email:
            stored = _room_booking_store.get(room.name, {})
            email = stored.get("email", "")
            if not email:
                return "No email stored. Ask user for email first."
        
        result, error = await _create_calendly_invitee(email, start_time)
        
        # Publish result in background
        asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps({
                    "type": "meeting_booked",
                    "booked": result is not None,
                    "start_time": start_time,
                    "email": email,
                    "error": error if not result else None,
                }).encode("utf-8"),
                topic="meeting_booked",
            )
        )
        
        if result:
            return "Meeting booked! User will get confirmation email."
        return f"Booking failed: {error}"
    
    return book_calendly_meeting


def prewarm(proc: JobProcess):
    """Ultra-light prewarm."""
    proc.userdata["knowledge"] = KNOWLEDGE_PROMPT
    logger.info("Worker prewarmed")


async def entrypoint(ctx: JobContext):
    start_time = time.perf_counter()
    
    # Connect immediately
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info(f"Connected in {time.perf_counter() - start_time:.2f}s")
    
    room = ctx.room
    
    # Define tools
    @function_tool(
        description="Save booking details after collecting email, phone, country."
    )
    async def store_booking_details(email: str, phone: str, country: str) -> str:
        if not EMAIL_REGEX.match(email.strip()):
            return "Invalid email format."
        
        data = {"email": email.strip(), "phone": phone.strip(), "country": country.strip()}
        _room_booking_store[room.name] = data
        
        # Publish in background
        asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps({"type": "booking_details", **data}).encode("utf-8"),
                topic=BOOKING_DATA_TOPIC,
            )
        )
        return "Booking details saved!"
    
    @function_tool(
        description="Get stored booking details."
    )
    async def get_stored_booking_details() -> str:
        stored = _room_booking_store.get(room.name)
        if not stored:
            return "No details stored yet."
        return f"Stored: email {stored['email']}"
    
    # IMPORTANT FIX: Create VAD and proper session configuration
    vad = silero.VAD.load()
    
    # Create session with proper configuration for listening
    session = AgentSession(
        vad=vad,  # Add VAD for listening
        llm=openai.realtime.RealtimeModel(
            model="gpt-4o-realtime-preview-2024-12-17",  # Use correct model name
            voice="alloy",
            temperature=0.8,
            turn_detection=ServerVad(
                type="server_vad",
                threshold=0.5,
                silence_duration_ms=500,  # Increased for better listening
                prefix_padding_ms=300,
            ),
        ),
    )
    
    agent = Agent(
        instructions=KNOWLEDGE_PROMPT,
        tools=[
            store_booking_details,
            get_stored_booking_details,
            check_calendly_availability,
            _make_book_calendly_meeting_tool(room)
        ],
    )
    
    # FIX: Wait for session to be fully ready
    logger.info("Starting session...")
    await session.start(agent=agent, room=room)
    logger.info(f"Session started in {time.perf_counter() - start_time:.2f}s")
    
    # Send greeting after session is ready
    try:
        await session.generate_reply(
            instructions="Say: Hey there! I'm Zia from Atlasium. How can I help you today?"
        )
        logger.info(f"Greeting sent in {time.perf_counter() - start_time:.2f}s")
    except Exception as e:
        logger.error(f"Failed to send greeting: {e}")
        # Retry once
        try:
            await asyncio.sleep(0.5)
            await session.generate_reply(
                instructions="Say: Hi! I'm Zia. How can I help you today?"
            )
        except Exception as e2:
            logger.error(f"Retry failed: {e2}")
    
    # Keep alive and monitor
    while True:
        await asyncio.sleep(1)
        # Optional: Add health check logging
        # logger.debug("Agent is active")


if __name__ == "__main__":
    logger.info("Starting voice agent...")
    
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            job_executor_type=JobExecutorType.PROCESS,
            num_idle_processes=1,
        ),
    )