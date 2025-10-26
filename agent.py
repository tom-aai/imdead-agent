from dotenv import load_dotenv

import json
import logging
import os
from datetime import datetime

from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions, RoomOutputOptions, metrics, MetricsCollectedEvent
from livekit.plugins import noise_cancellation, silero, hedra
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from utils import load_prompt

load_dotenv(".env.local")

logger = logging.getLogger("agent")

# Create chat history directory if it doesn't exist
chat_history_dir = "chat_history"
os.makedirs(chat_history_dir, exist_ok=True)

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=load_prompt('onboarding_prompt.txt'),
        )


async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        stt="assemblyai/universal-streaming:en",
        llm="openai/gpt-4.1-mini",
        #tts="cartesia/sonic-2:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        tts="cartesia/sonic-2:7392e429-4eb4-4abb-b40b-da9fdecbc5ce",
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    # Metrics collection, to measure pipeline performance
    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Usage: {summary}")

    async def save_chat_history():
        """Save the complete chat history when the session ends"""
        try:
            current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(chat_history_dir, f"chat_history_{ctx.room.name}_{current_time}.json")
            
            # Save the complete conversation history
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(session.history.to_dict(), f, indent=2, ensure_ascii=False)
                
            logger.info(f"Chat history for {ctx.room.name} saved to {filename}")
            
        except Exception as e:
            logger.error(f"Error saving chat history: {e}")

    ctx.add_shutdown_callback(log_usage)
    ctx.add_shutdown_callback(save_chat_history)

    avatar = hedra.AvatarSession(
        avatar_id="eb423485-fa83-4a52-92ed-e6afffb87e20",  
    )

    # Start the avatar and wait for it to join
    await avatar.start(session, room=ctx.room)

    await session.start(
        room=ctx.room,
        agent=Assistant(),
    )

    await session.generate_reply()


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
