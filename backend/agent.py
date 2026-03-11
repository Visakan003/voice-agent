import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
)

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


def prewarm(proc: JobProcess):
    """
    Runs once when worker starts.
    Useful for loading models or warming dependencies.
    """
    logger.info("Worker prewarm complete")


async def entrypoint(ctx: JobContext):

    logger.info("Connecting to LiveKit room...")

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    logger.info("Connected to room: %s", ctx.room.name)

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime-1.5",
            voice="marin",
            speed=0.9,
            turn_detection=ServerVad(
                type="server_vad",
                threshold=0.6,
                silence_duration_ms=450,
                prefix_padding_ms=300,
                create_response=True,
                interrupt_response=True,
            ),
        ),
    )

    agent = Agent(
        instructions=KNOWLEDGE_PROMPT
    )

    await session.start(
        agent=agent,
        room=ctx.room
    )

    logger.info("Agent session started")

    await session.generate_reply(
        instructions="Say exactly once: Hey there! I'm Zia from Atlasium. How can I help you today?"
    )

    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":

    logger.info("Starting LiveKit voice agent worker...")

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            job_executor_type=JobExecutorType.THREAD,
            num_idle_processes=0,
        ),
    )