import asyncio
import logging
import os
from urllib.parse import urlparse

import aiohttp
from dotenv import load_dotenv
import aiohttp.resolver as aiohttp_resolver
import aiohttp.connector as aiohttp_connector

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
    ChatMessage,
)
from livekit.agents.voice_assistant import VoiceAssistant
from livekit.plugins import openai, silero
from livekit.agents.worker import JobExecutorType

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

# Add file handler for better debugging
file_handler = logging.FileHandler('voice-assistant.log')
file_handler.setLevel(logging.INFO)
logger.addHandler(file_handler)

# Also log to console
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
logger.addHandler(console_handler)


_ORIGINAL_WS_CONNECT = aiohttp.ClientSession.ws_connect


async def _patched_ws_connect(self, url, *args, **kwargs):
    """
    Local workaround: bypass TLS hostname verification for LiveKit websocket hosts.
    """
    skip_verify = os.getenv("LIVEKIT_SKIP_SSL_VERIFY", "1").strip().lower() in {"1", "true", "yes"}
    parsed = urlparse(str(url))
    host = parsed.hostname or ""
    if skip_verify and host.endswith(".livekit.cloud") and "ssl" not in kwargs:
        kwargs["ssl"] = False
        logger.warning("LIVEKIT TLS verification disabled for host: %s", host)
    return await _ORIGINAL_WS_CONNECT(self, url, *args, **kwargs)


aiohttp.ClientSession.ws_connect = _patched_ws_connect


def prewarm(proc: JobProcess):
    """Pre-warm function called when the worker process starts."""
    logger.info("Prewarming process")
    # Preload silero VAD model
    silero.VAD.load()


async def entrypoint(ctx: JobContext):
    logger.info("Connecting to room: %s", ctx.room.name)
    
    # Connect to the room
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Wait for participant to join
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined: {participant.identity}")
    
    # Initialize the LLM
    openai_llm = openai.LLM(
        model="gpt-4o-mini",  # Using standard model for now
    )
    
    # Initialize the voice assistant
    assistant = VoiceAssistant(
        vad=silero.VAD(),  # Voice Activity Detection
        stt=openai.STT(),  # Speech to Text
        llm=openai_llm,    # Language Model
        tts=openai.TTS(),  # Text to Speech
        chat_ctx=llm.ChatContext().append(
            role="system",
            text=KNOWLEDGE_PROMPT,
        ),
    )
    
    # Start the assistant
    assistant.start(ctx.room)
    
    logger.info("Voice assistant started for room %s", ctx.room.name)
    
    # Greet the user after a short delay
    await asyncio.sleep(1)
    await assistant.say("Hey there! I'm Zia from Atlasium. How can I help you today?", allow_interruptions=True)
    
    logger.info("Greeting sent")
    
    # Keep the agent alive
    try:
        while True:
            await asyncio.sleep(1)
            
            # Log assistant state periodically for debugging
            if hasattr(assistant, '_state'):
                logger.debug(f"Assistant state: {assistant._state}")
                
    except asyncio.CancelledError:
        logger.info("Agent session cancelled")
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}")
    finally:
        logger.info("Cleaning up agent session")
        assistant.stop()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            job_executor_type=JobExecutorType.PROCESS,
            num_idle_processes=1,
            # Add these for better debugging
            agent_name="zia-voice-assistant",
        ),
    )