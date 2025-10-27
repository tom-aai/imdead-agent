from dotenv import load_dotenv

import json
import logging
import os
from datetime import datetime

from livekit import agents, rtc
from livekit.agents import AgentSession, Agent, metrics, MetricsCollectedEvent
from livekit.plugins import silero, hedra
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
    logger.info("🚀 Agent starting up...")
    logger.info(f"Connecting to room {ctx.room.name}")
    
    # Connect to the room
    await ctx.connect()
    logger.info("✅ Connected to room, waiting for participants...")
    
    # Wait for the first participant to join (IVR pattern)
    participant = await ctx.wait_for_participant()
    logger.info(f"👤 Participant joined: {participant.identity}")
    
    # Log all participant info for debugging
    logger.info(f"🔍 Participant info - Identity: {participant.identity}")
    logger.info(f"🔍 Participant info - Name: {participant.name}")
    logger.info(f"🔍 Participant info - Metadata: {participant.metadata}")
    
    # Get pictureId from participant metadata (set during token generation)
    # Metadata is a JSON string that we need to parse
    picture_id = None
    if participant.metadata:
        try:
            metadata = json.loads(participant.metadata)
            picture_id = metadata.get("pictureId")
            logger.info(f"📦 Parsed metadata: {metadata}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse participant metadata: {e}")
            logger.error(f"Raw metadata: {participant.metadata}")
    
    if picture_id:
        logger.info(f"🖼️  Picture ID from participant metadata: {picture_id}")
        print(f"\n{'='*60}")
        print(f"✅ PICTURE ID RECEIVED: {picture_id}")
        print(f"{'='*60}\n")
    else:
        logger.warning(f"⚠️  No pictureId found in participant metadata for {participant.identity}")
        logger.info(f"Raw metadata: {participant.metadata}")
    
    # Create the agent session for this participant
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
            
            # Include picture_id in filename if available
            if picture_id:
                filename = os.path.join(chat_history_dir, f"chat_history_{picture_id}_{current_time}.json")
            else:
                filename = os.path.join(chat_history_dir, f"chat_history_{ctx.room.name}_{current_time}.json")
            
            # Save the complete conversation history with metadata
            history_data = session.history.to_dict()
            
            # Add session metadata including picture_id
            session_metadata = {
                "room_name": ctx.room.name,
                "picture_id": picture_id,
                "timestamp": current_time,
                "conversation": history_data
            }
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(session_metadata, f, indent=2, ensure_ascii=False)
                
            logger.info(f"Chat history for {ctx.room.name} (picture_id: {picture_id}) saved to {filename}")
            
        except Exception as e:
            logger.error(f"Error saving chat history: {e}")

    ctx.add_shutdown_callback(log_usage)
    ctx.add_shutdown_callback(save_chat_history)

    # Start avatar
    logger.info("🎭 Starting avatar...")
    avatar = hedra.AvatarSession(
        avatar_id="eb423485-fa83-4a52-92ed-e6afffb87e20",  
    )
    await avatar.start(session, room=ctx.room)
    logger.info("✅ Avatar started")

    # Start the session - this blocks and keeps the agent alive
    logger.info("🤖 Starting agent session...")
    await session.start(
        room=ctx.room,
        agent=Assistant(),
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
