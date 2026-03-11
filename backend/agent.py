import asyncio
import logging
import os
import time
from pathlib import Path
from dotenv import load_dotenv
import aiohttp.resolver as aiohttp_resolver
import aiohttp.connector as aiohttp_connector

from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
)
from livekit.agents import worker as livekit_worker

from livekit.agents.worker import JobExecutorType
from livekit.plugins import openai
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

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


def prewarm(proc: JobProcess):
    logger.info("Worker prewarm complete")


async def entrypoint(ctx: JobContext):
    started_at = time.perf_counter()

    logger.info("Connecting to LiveKit room...")
    logger.info("Job room URL: %s", ctx._info.url)

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    logger.info("Connected to room: %s (%.2fs)", ctx.room.name, time.perf_counter() - started_at)

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

    agent = Agent(instructions=KNOWLEDGE_PROMPT)

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