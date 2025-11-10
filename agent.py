from dotenv import load_dotenv

import aiohttp
import json
import logging
import os
from datetime import datetime

from livekit import agents, rtc
from livekit.agents import AgentSession, Agent, metrics, MetricsCollectedEvent
from livekit.plugins import silero, hedra, google
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from supabase import create_client, Client

from utils import load_prompt

load_dotenv(".env.local")

logger = logging.getLogger("agent")

# Create chat history directory if it doesn't exist
chat_history_dir = "chat_history"
os.makedirs(chat_history_dir, exist_ok=True)

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5001")

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    logger.info("✅ Supabase client initialized")
else:
    logger.warning("⚠️ Supabase credentials not found - will only save to local files")

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=load_prompt('onboarding_prompt.txt'),
        )


async def entrypoint(ctx: agents.JobContext):
    logger.info("Agent starting up...")
    
    # Connect to the room
    await ctx.connect()
    
    # Wait for the first participant to join (IVR pattern)
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined: {participant.identity}")
    
    # Get user info and pictureId from participant metadata (set during token generation)
    picture_id = None
    user_info = {}
    
    if participant.metadata:
        try:
            metadata = json.loads(participant.metadata)
            raw_picture_id = metadata.get("pictureId")
            user_info = metadata.get("user", {})
            
            # Append .png to picture_id for the actual file reference
            if raw_picture_id:
                picture_id = f"{raw_picture_id}.png"
            
            # Log user information
            logger.info("=" * 50)
            logger.info("👤 USER CONNECTED TO AGENT")
            logger.info("=" * 50)
            logger.info(f"User ID: {user_info.get('userId', 'N/A')}")
            logger.info(f"Name: {user_info.get('name', 'N/A')}")
            logger.info(f"First Name: {user_info.get('firstName', 'N/A')}")
            logger.info(f"Last Name: {user_info.get('lastName', 'N/A')}")
            logger.info(f"Email: {user_info.get('email', 'N/A')}")
            logger.info(f"Picture ID: {picture_id or 'N/A'}")
            logger.info("=" * 50)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse participant metadata: {e}")
    
    # Create the agent session for this participant
    session = AgentSession(
        stt="assemblyai/universal-streaming:en",
        llm="openai/gpt-4.1-mini",
        tts="cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        #tts="cartesia/sonic-2:7392e429-4eb4-4abb-b40b-da9fdecbc5ce", # me
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
            
            # Get room session ID (coroutine property that needs to be awaited)
            room_sid = await ctx.room.sid
            
            # Include user_id in filename if available, otherwise picture_id
            user_id = user_info.get('userId', '')
            if user_id:
                filename = os.path.join(chat_history_dir, f"chat_history_{user_id}_{current_time}.json")
            elif picture_id:
                filename = os.path.join(chat_history_dir, f"chat_history_{picture_id}_{current_time}.json")
            else:
                filename = os.path.join(chat_history_dir, f"chat_history_{ctx.room.name}_{current_time}.json")
            
            # Save the complete conversation history with metadata
            history_data = session.history.to_dict()
            
            # Get usage summary
            usage_summary = usage_collector.get_summary()
            usage_data = {
                "llm_prompt_tokens": usage_summary.llm_prompt_tokens,
                "llm_prompt_cached_tokens": usage_summary.llm_prompt_cached_tokens,
                "llm_completion_tokens": usage_summary.llm_completion_tokens,
                "tts_characters_count": usage_summary.tts_characters_count,
                "tts_audio_duration": usage_summary.tts_audio_duration,
                "stt_audio_duration": usage_summary.stt_audio_duration,
            }
            
            # Add session metadata including user info, picture_id, and LiveKit session info
            session_metadata = {
                # LiveKit session identifiers
                "session_id": room_sid,  # Unique LiveKit session/room SID
                "job_id": ctx.job.id,
                
                # User-specific metadata
                "picture_id": picture_id or '',
                "user_id": user_info.get('userId') or '',
                "user_firstname": user_info.get('firstName') or '',
                "user_lastname": user_info.get('lastName') or '',
                
                # Timestamps
                "timestamp": current_time,
                
                # Usage metrics
                "usage": usage_data,
                
                # Conversation data
                "conversation": history_data
            }
            
            # 1. Save to local JSON file (backup)
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(session_metadata, f, indent=2, ensure_ascii=False)
            logger.info(f"📁 Chat history saved to {filename}")
            
            # 2. Save to Supabase database
            if supabase and user_id:
                try:
                    # Prepare data for Supabase (matches your table schema)
                    supabase_data = {
                        "session_id": room_sid,
                        "job_id": ctx.job.id,
                        "picture_id": picture_id or '',
                        "user_id": user_id,
                        "user_firstname": user_info.get('firstName') or '',
                        "user_lastname": user_info.get('lastName') or '',
                        "timestamp": current_time,
                        "usage": usage_data,
                        "conversation": history_data
                    }
                    
                    # Insert into Supabase
                    response = supabase.table("interview_calls").insert(supabase_data).execute()
                    
                    logger.info("=" * 50)
                    logger.info("✅ DATA SAVED TO SUPABASE")
                    logger.info("=" * 50)
                    logger.info(f"  Session ID: {room_sid}")
                    logger.info(f"  Job ID: {ctx.job.id}")
                    logger.info(f"  User: {user_info.get('name', 'N/A')} ({user_info.get('email', 'N/A')})")
                    logger.info(f"  Picture ID: {picture_id or 'N/A'}")
                    logger.info(f"  Conversation items: {len(history_data.get('items', []))}")
                    logger.info(f"  LLM tokens: {usage_summary.llm_prompt_tokens} + {usage_summary.llm_completion_tokens}")
                    logger.info(f"  Audio duration: TTS={usage_summary.tts_audio_duration:.1f}s, STT={usage_summary.stt_audio_duration:.1f}s")
                    logger.info("=" * 50)
                    
                    # Trigger backend post-processing
                    try:
                        async with aiohttp.ClientSession() as http_session:
                            async with http_session.post(
                                f"{BACKEND_URL}/process-transcript",
                                json={"session_id": room_sid},
                                timeout=aiohttp.ClientTimeout(total=10)
                            ) as response:
                                if response.status == 200:
                                    logger.info(f"✅ Backend notified successfully for session {room_sid}")
                                else:
                                    logger.warning(f"⚠️ Backend returned status {response.status}")
                    except Exception as backend_error:
                        logger.error(f"❌ Error notifying backend: {backend_error}")
                        logger.info("💾 Transcript is still saved in Supabase for later processing")
                    
                except Exception as supabase_error:
                    logger.error(f"❌ Error saving to Supabase: {supabase_error}")
                    logger.info("💾 Data is still saved in local JSON file as backup")
            elif not user_id:
                logger.warning("⚠️ No user_id available - skipping Supabase upload")
            
        except Exception as e:
            logger.error(f"❌ Error in save_chat_history: {e}")

    ctx.add_shutdown_callback(log_usage)
    ctx.add_shutdown_callback(save_chat_history)

    # Start avatar
    avatar = hedra.AvatarSession(
        #avatar_id="eb423485-fa83-4a52-92ed-e6afffb87e20",   
        avatar_id="af7b8de9-a7ae-48c4-86ab-6affd3485727", 
    )
    await avatar.start(session, room=ctx.room)

    # Start the session - this blocks and keeps the agent alive
    await session.start(
        room=ctx.room,
        agent=Assistant(),
    )

    await session.generate_reply(
        instructions="Greet the user and tell them about the agenda for the call."
    )        

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
