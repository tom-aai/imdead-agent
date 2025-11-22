from dotenv import load_dotenv

import aiohttp
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from livekit import agents, rtc
from livekit.agents import AgentSession, Agent, metrics, MetricsCollectedEvent
from livekit.plugins import silero, hedra, google
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from supabase import create_client, Client

from utils import load_prompt

load_dotenv(".env.local")

logger = logging.getLogger("agent")

# Suppress noisy hpack (HTTP/2) debug logs
logging.getLogger("hpack").setLevel(logging.WARNING)

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

@dataclass
class UserProfileData:
    """Store user profile data for personalized prompts."""
    myers_briggs_code: Optional[str] = None
    personality: Optional[str] = None
    expertise_description: Optional[str] = None

class OnboardingAgent(Agent):
    """Agent for new users going through onboarding."""
    def __init__(self) -> None:
        super().__init__(
            instructions=load_prompt('onboarding_prompt.txt'),
        )

class InterviewAgent(Agent):
    """Agent for returning users going through interviews."""
    def __init__(self) -> None:
        super().__init__(
            instructions="",  # Will be set dynamically in on_enter()
        )
    
    async def on_enter(self) -> None:
        """Called when the agent is first activated. Injects user-specific context."""
        logger.info("InterviewAgent activated - loading personalized instructions")
        
        # Get user profile data from session userdata
        user_profile = getattr(self.session, 'userdata', None)
        
        # Load base prompt
        base_prompt = load_prompt('interview_prompt.txt')
        
        # Build user context section
        user_context = ""
        if user_profile and isinstance(user_profile, UserProfileData):
            context_parts = []
            
            if user_profile.myers_briggs_code:
                context_parts.append(f"**Myers-Briggs Type:** {user_profile.myers_briggs_code}")
            
            if user_profile.personality:
                context_parts.append(f"**Personality Profile:** {user_profile.personality}")
            
            if user_profile.expertise_description:
                context_parts.append(f"**Area of Expertise:** {user_profile.expertise_description}")
            
            if context_parts:
                user_context = "\n".join(context_parts)
                logger.info(f"✅ Loaded user profile data for personalized prompt")
            else:
                user_context = "No user profile data available yet - this may be their first interview after onboarding."
                logger.warning("⚠️ User profile exists but has no data")
        else:
            user_context = "No user profile data available yet - this may be their first interview after onboarding."
            logger.warning("⚠️ No user profile data found in session")
        
        # Inject user context into the prompt
        personalized_prompt = base_prompt.replace(
            "[INJECT USER CONTEXT AND EXPERTISE HERE]",
            user_context
        )
        
        # Update the agent's instructions
        await self.update_instructions(personalized_prompt)


async def check_if_returning_user(user_id: str) -> bool:
    """
    Check if a user has completed onboarding by looking for previous interview calls.
    Returns True if user has at least one completed interview call in the database.
    """
    if not supabase or not user_id:
        logger.warning("Cannot check returning user status - no Supabase client or user_id")
        return False
    
    try:
        # Query for any previous interview calls for this user
        response = supabase.table("interview_calls").select("session_id").eq("user_id", user_id).limit(1).execute()
        
        has_previous_calls = len(response.data) > 0
        logger.info(f"User {user_id} returning status: {has_previous_calls}")
        return has_previous_calls
    except Exception as e:
        logger.error(f"Error checking returning user status: {e}")
        return False


async def fetch_user_profile(user_id: str) -> Optional[UserProfileData]:
    """
    Fetch user profile data from Supabase for personalized prompts.
    Returns UserProfileData with myers_briggs_code, personality, and expertise_description.
    """
    if not supabase or not user_id:
        logger.warning("Cannot fetch user profile - no Supabase client or user_id")
        return None
    
    try:
        # Query user_profiles table for this user
        response = supabase.table("user_profiles").select(
            "myers_briggs_code, personality, expertise_description"
        ).eq("user_id", user_id).limit(1).execute()
        
        if response.data and len(response.data) > 0:
            profile_data = response.data[0]
            user_profile = UserProfileData(
                myers_briggs_code=profile_data.get("myers_briggs_code"),
                personality=profile_data.get("personality"),
                expertise_description=profile_data.get("expertise_description")
            )
            logger.info(f"✅ Fetched user profile for user {user_id}")
            logger.info(f"   Myers-Briggs: {user_profile.myers_briggs_code or 'None'}")
            logger.info(f"   Personality: {user_profile.personality[:50] if user_profile.personality else 'None'}...")
            logger.info(f"   Expertise: {user_profile.expertise_description[:50] if user_profile.expertise_description else 'None'}...")
            return user_profile
        else:
            logger.info(f"No user profile found for user {user_id}")
            return None
    except Exception as e:
        logger.error(f"Error fetching user profile: {e}")
        return None


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
    
    # Check if user is returning to determine which agent to use
    user_id = user_info.get('userId', '')
    is_returning_user = await check_if_returning_user(user_id)
    
    # Fetch user profile data for personalized prompts (for returning users)
    user_profile = None
    if is_returning_user:
        user_profile = await fetch_user_profile(user_id)
    
    # Select the appropriate agent based on user status
    if is_returning_user:
        logger.info(f"Returning user detected - using InterviewAgent")
        initial_agent = InterviewAgent()
    else:
        logger.info(f"New user detected - using OnboardingAgent")
        initial_agent = OnboardingAgent()
    
    # Create the agent session for this participant
    # Pass user_profile as userdata for InterviewAgent to access
    session = AgentSession(
        userdata=user_profile,
        stt="assemblyai/universal-streaming:en",
        llm="openai/gpt-4.1-mini",
        tts="inworld/inworld-tts-1:Olivia", #Olivia on tts-1-max is good. Sarah, Ronald, Edward
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
                    
                    # Also save to user_avatar_images table if we have picture_id
                    if picture_id:
                        try:
                            avatar_image_data = {
                                "user_id": user_id,
                                "picture_id": picture_id,
                                "picture_source": "from_interview_session"
                            }
                            supabase.table("user_avatar_images").insert(avatar_image_data).execute()
                            logger.info(f"✅ Avatar image reference saved for user {user_id}: {picture_id}")
                        except Exception as avatar_error:
                            logger.warning(f"⚠️ Could not save avatar image reference: {avatar_error}")
                    
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
                    
                    # Trigger post-call orchestrator (fire and forget)
                    try:
                        async with aiohttp.ClientSession() as http_session:
                            # Just trigger the orchestrator - don't wait for it to complete
                            # The orchestrator runs independently in the backend
                            async with http_session.post(
                                f"{BACKEND_URL}/post-call-orchestrator",
                                json={"session_id": room_sid},
                                timeout=aiohttp.ClientTimeout(total=5)  # Short timeout just to confirm connection
                            ) as response:
                                if response.status == 200:
                                    logger.info(f"✅ Post-call orchestrator triggered for session {room_sid}")
                                else:
                                    logger.warning(f"⚠️ Post-call orchestrator returned status {response.status}")
                    except Exception as backend_error:
                        logger.warning(f"⚠️ Could not trigger post-call orchestrator: {backend_error}")
                        logger.info("💾 Transcript is saved in Supabase and can be processed manually if needed")
                    
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

    # Start the session with the selected agent - this blocks and keeps the agent alive
    await session.start(
        room=ctx.room,
        agent=initial_agent,
    )

    await session.generate_reply(
        instructions="Greet the user and tell them about the agenda for the call."
    )        

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
