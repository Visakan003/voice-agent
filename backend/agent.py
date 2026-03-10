import asyncio
import logging
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

from pathlib import Path

KNOWLEDGE_FILE = Path(__file__).parent / "knowledge.txt"
KNOWLEDGE_PROMPT = KNOWLEDGE_FILE.read_text(encoding="utf-8")

load_dotenv()

logger = logging.getLogger("voice-assistant")
logger.setLevel(logging.INFO)


def prewarm(proc: JobProcess):
    pass


async def entrypoint(ctx: JobContext):

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime-1.5",
            voice="marin",
            speed=0.90,
            turn_detection=ServerVad(
                type="server_vad",
                threshold=0.6,  # Higher = less sensitive; filters background noise, only clear speech triggers
                silence_duration_ms=450,  # Longer silence before turn ends; avoids reacting to brief noises
                prefix_padding_ms=300,
                create_response=True,
                interrupt_response=True,
            ),
        ),
    )

    # agent = Agent(
    #     instructions=(
    #         "You are Zia, the AI sales agent for Atlasium 7/88 AI. "
    #         "Speak naturally like a real person — use casual language, contractions, and a conversational tone. "
    #         "Avoid sounding robotic or scripted. Use natural filler phrases like 'sure', 'of course', 'absolutely'. "
    #         "Keep responses short and friendly. Always respond in English only.\n\n"

    #         "COMMUNICATION STYLE:\n"
    #         "- Warm, confident, conversational tone.\n"
    #         "- Use contractions.\n"
    #         "- Keep responses short (1-4 sentences).\n"
    #         "- Ask follow-up questions.\n\n"

    #         "STRICT KNOWLEDGE RULES:\n"
    #         "- Only use information provided.\n"
    #         "- Do NOT invent details.\n"
    #         "- If outside knowledge say: "
    #         "'I'm not familiar with that. I can only help with Atlasium 7/88 AI services.'\n\n"

    #         "BOOKING RULES:\n"
    #         "- Do NOT schedule meetings.\n"
    #         "- Direct users to fill the demo form on the website.\n\n"

    #         "PRICING RULES:\n"
    #         "- Never provide pricing.\n"
    #         "- Say pricing is discussed during the walkthrough.\n\n"

    #         "COMPANY KNOWLEDGE:\n"
    #         "Atlasium 7/88 AI is a full-stack AI automation platform for businesses. "
    #         "It handles lead generation, booking, sales follow-up, and operations automation.\n\n"

    #         "CORE PRODUCTS:\n"
    #         "- XipherX: AI lead generation engine.\n"
    #         "- DialZia: AI call, text, and email booking assistant.\n"
    #         "- CoreIQ: Data intelligence and CRM hub.\n\n"

    #         "FLOW:\n"
    #         "XipherX finds leads → DialZia engages → CoreIQ organizes and tracks.\n\n"

    #         "INDUSTRIES:\n"
    #         "Real estate, mortgage, insurance, law firms, clinics, contractors, ecommerce, SaaS, agencies.\n\n"

    #         "PRONUNCIATION GUIDE:\n"
    #         "Atlasium = At-LAY-zee-um\n"
    #         "XipherX = ZY-fer-X\n"
    #         "DialZia = Dial-Zee-ah\n"
    #         "CoreIQ = Core-I-Q\n"
    #     ),
    # )
    agent = Agent(
        instructions=KNOWLEDGE_PROMPT,
    )

    await session.start(agent=agent, room=ctx.room)

    logger.info("Agent session started for room %s", ctx.room.name)

    # Greet first via generate_reply so the session stays in the right state (listening + speaking).
    await session.generate_reply(
        instructions="Say exactly once: Hey there! I'm Zia from Atlasium. How can I help you today?"
    )

    # Keep agent alive
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            job_executor_type=JobExecutorType.PROCESS,
            num_idle_processes=1,
        ),
    )