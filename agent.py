from dotenv import load_dotenv

import json
import logging

from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions, RoomOutputOptions, metrics, MetricsCollectedEvent
from livekit.plugins import noise_cancellation, silero, hedra
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from utils import load_prompt

load_dotenv(".env.local")

logger = logging.getLogger("agent")

ctx_history_file = "ctx_history.json"

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
        """Save the chat history to ctx_history_file when the call ends."""
        try:
            # Use the native to_dict method from ChatContext
            chat_data = session.history.to_dict()
            
            # Save to file
            with open(ctx_history_file, 'w', encoding='utf-8') as f:
                json.dump(chat_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Chat history saved to {ctx_history_file}")
            
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
