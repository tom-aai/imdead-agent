from dotenv import load_dotenv

import json
import logging
import os
import uuid
from typing import Optional

from livekit import agents, rtc, api
from livekit.agents import AgentSession, Agent, metrics, MetricsCollectedEvent, RunContext
from livekit.agents.llm import function_tool
from livekit.plugins import silero, hedra
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from supabase import create_client, Client

from utils import load_prompt
from services.gender_detection import GenderDetector
from services.user_data import UserDataService, UserProfileData

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
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY")

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    logger.info("Supabase client initialized")
else:
    logger.warning("Supabase credentials not found - will only save to local files")

# Initialize user data service
user_service = UserDataService(supabase)

# Initialize gender detector (reuses Anthropic client across calls)
gender_detector = GenderDetector()

async def start_room_recording(room_name: str, user_id: str, participant_identity: str) -> Optional[str]:
    """
    Start recording ONLY the user's audio to Supabase storage.
    Uses ParticipantEgress to record just the user participant.
    Recording automatically stops when the participant leaves.
    
    Returns:
        Optional[str]: The recording UUID if recording started successfully, None otherwise
    """
    # Generate a unique UUID for this recording
    recording_id = str(uuid.uuid4())
    
    # Get Supabase storage credentials
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_project_id = supabase_url.split("//")[1].split(".")[0] if supabase_url else None
    s3_access_key_id = os.getenv("SUPABASE_S3_ACCESS_KEY_ID")
    s3_secret_access_key = os.getenv("SUPABASE_S3_SECRET_ACCESS_KEY")
    s3_region = os.getenv("SUPABASE_S3_REGION", "auto")
    
    if not all([supabase_project_id, s3_access_key_id, s3_secret_access_key]):
        logger.warning("⚠️ Supabase S3 credentials not found - skipping recording")
        return None
    
    # Construct the S3-compatible endpoint for Supabase storage
    storage_endpoint = f"https://{supabase_project_id}.storage.supabase.co/storage/v1/s3"
    filename = f"{user_id}/{recording_id}.mp4"
    
    try:
        # Create the egress request for participant recording (user only)
        # ParticipantEgress waits for the participant to join and records their audio
        req = api.ParticipantEgressRequest(
            room_name=room_name,
            identity=participant_identity,  # Only record this participant (the user)
            screen_share=False,  # Don't record screen share
            file_outputs=[
                api.EncodedFileOutput(
                    filepath=filename,
                    s3=api.S3Upload(
                        endpoint=storage_endpoint,
                        bucket="interview-recordings",
                        region=s3_region,
                        access_key=s3_access_key_id,
                        secret=s3_secret_access_key,
                        force_path_style=True,
                    ),
                )
            ],
        )
        
        logger.info("=" * 50)
        logger.info("🎙️ STARTING USER AUDIO RECORDING")
        logger.info("=" * 50)
        logger.info(f"  Recording ID: {recording_id}")
        logger.info(f"  Room: {room_name}")
        logger.info(f"  Participant: {participant_identity}")
        logger.info(f"  User ID: {user_id}")
        logger.info(f"  Recording: User audio only (no agent)")
        logger.info(f"  Destination: interview-recordings/{user_id}/{recording_id}.mp4")
        logger.info("=" * 50)
        
        # Start the egress
        lkapi = api.LiveKitAPI()
        response = await lkapi.egress.start_participant_egress(req)
        await lkapi.aclose()
        
        logger.info(f"✅ Recording started - Egress ID: {response.egress_id}")
        return recording_id
        
    except Exception as e:
        logger.error(f"❌ Error starting participant recording: {e}")
        logger.exception("Full traceback:")
        return None


class OnboardingAgent(Agent):
    """Agent for new users going through onboarding."""
    def __init__(self, job_context=None, agent_name: str = "Ashley") -> None:
        self.job_context = job_context
        self.agent_name = agent_name
        
        # Load prompt and inject agent name
        base_prompt = load_prompt('onboarding_prompt.txt')
        personalized_prompt = base_prompt.replace("[AGENT_NAME]", agent_name)
        
        super().__init__(
            instructions=personalized_prompt,
        )
    
    async def on_enter(self) -> None:
        """Called when the agent is first activated."""
        logger.info("OnboardingAgent activated")
        # Generate initial greeting
        await self.session.generate_reply(
            instructions="Greet the user and explain the onboarding process."
        )

    @function_tool
    async def end_call(self, context: RunContext):
        """End the onboarding call. Use this tool when you have completed all 5 onboarding 
        objectives and said goodbye to the user. This will gracefully end the call and 
        clean up the session.
        
        IMPORTANT: Before calling this tool, you MUST:
        1. Have covered all 5 onboarding objectives (expertise, background, common mistakes, what they're building, problem they're solving)
        2. Thank the user for the conversation
        3. Tell them their expert avatar will be created from this onboarding
        4. Say goodbye
        
        Only call this tool AFTER you have said your final goodbye message to the user.
        """
        if not self.job_context:
            logger.error("No job context available for end_call")
            return None, "Failed to end call: No job context available"
        
        room_name = self.job_context.room.name
        logger.info(f"Ending onboarding call - deleting room {room_name}")
        
        try:
            await self.job_context.api.room.delete_room(
                api.DeleteRoomRequest(room=room_name)
            )
            logger.info(f"Successfully ended call and deleted room {room_name}")
            return None, "Call ended successfully."
        except Exception as e:
            logger.error(f"Error ending call: {e}")
            return None, f"Failed to end call: {e}"

class InterviewAgent(Agent):
    """Agent for returning users going through interviews."""
    def __init__(self, job_context=None, agent_name: str = "Ashley") -> None:
        self.job_context = job_context
        self.agent_name = agent_name
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
        
        # Inject agent name into the prompt
        personalized_prompt = personalized_prompt.replace(
            "[AGENT_NAME]",
            self.agent_name
        )
        
        # Update the agent's instructions
        await self.update_instructions(personalized_prompt)

        # initial greeting.
        await self.session.generate_reply(
            instructions="Greet the user and tell them the agenda for the call."
        )

    
    @function_tool
    async def transfer_to_onboarding(self, context: RunContext):
        """Transfer the user to the onboarding agent. Use this tool when the user explicitly 
        requests to go through the onboarding process again, when they want to update their 
        profile information that was gathered during onboarding, or when they want to record their voice for a new voice clone. 
        
        Examples of when to use this tool:
        - User: "I want to redo the onboarding process"
        - User: "Can I update my profile information?"
        - User: "Let me go through onboarding again"
        - User: "Can I create a new clone of my voice?"
        
        Always confirm with the user before transferring them, making sure to mention that previous onboarding data is not deleted but only additional data is added.
        """
        logger.info("🔄 Transfer to onboarding agent requested")
        
        # Inform the user about the transfer
        await self.session.say("Ok, let's go through the onboarding process again. Hold on a few moments while I find my script.")
        
        # Switch to the onboarding agent (preserve the same agent name/persona)
        self.session.update_agent(OnboardingAgent(job_context=self.job_context, agent_name=self.agent_name))
        
        # No generation required after the transfer - the new agent will handle it
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
    is_returning_user = await user_service.check_if_returning_user(user_id)
    
    # Fetch user profile data for personalized prompts (for returning users)
    user_profile = None
    if is_returning_user:
        user_profile = await user_service.fetch_user_profile(user_id)
    
    # Detect gender from first name to select appropriate avatar and voice
    first_name = user_info.get('firstName', '')
    detected_gender = gender_detector.detect_gender(first_name)
    
    # Select avatar, voice, and agent name based on gender
    # If male -> use Ashley (female avatar/voice)
    # If female -> use Phil (male avatar/voice)
    if detected_gender == "female":
        avatar_id = "af7b8de9-a7ae-48c4-86ab-6affd3485727"  # Ashley
        tts_voice = "inworld/inworld-tts-1:Ashley"  # Ashley's voice
        agent_name = "Ashley"
        logger.info(f"Male name detected - using Ashley avatar and Ashley voice")
    else:  # female
        #avatar_id = "f9ee171e-c671-439a-95d3-a0bc1e320c27"  # Phil
        avatar_id = "09392442-ad79-4f3a-b646-fbe20a6d616f"  # Me
        tts_voice = "inworld/inworld-tts-1:Edward"  # Phil's voice
        #tts_voice = "cartesia/sonic-3:6d6cc6bf-a98a-4db8-bd4e-43293bb49e1b"
        agent_name = "Phil"
        logger.info(f"Female name detected - using Phil avatar and male voice")
    
    # Select the appropriate agent based on user status
    if is_returning_user:
        logger.info(f"Returning user detected - using InterviewAgent")
        initial_agent = InterviewAgent(job_context=ctx, agent_name=agent_name)
        call_type = "interview"
    else:
        logger.info(f"New user detected - using OnboardingAgent")
        initial_agent = OnboardingAgent(job_context=ctx, agent_name=agent_name)
        call_type = "onboarding"
    
    # Metrics collection, to measure pipeline performance
    usage_collector = metrics.UsageCollector()

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Usage: {summary}")

    async def save_chat_history():
        """Wrapper to save chat history when the session ends"""
        # Get room session ID (coroutine property that needs to be awaited)
        room_sid = await ctx.room.sid
        
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
        
        # Delegate to service
        await user_service.save_chat_history(
            session_id=room_sid,
            job_id=ctx.job.id,
            room_name=ctx.room.name,
            user_info=user_info,
            picture_id=picture_id,
            conversation_data=history_data,
            usage_data=usage_data,
            usage_summary=usage_summary,
            chat_history_dir=chat_history_dir,
            backend_url=BACKEND_URL,
            recording_id=recording_id,
            call_type=call_type,
            backend_api_key=BACKEND_API_KEY
        )

    # Create the agent session for this participant
    # Pass user_profile as userdata for InterviewAgent to access
    session = AgentSession(
        userdata=user_profile,
        stt="assemblyai/universal-streaming:en",
        llm="openai/gpt-4.1-mini",
        tts=tts_voice,  # Dynamically selected based on user's gender
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    # Register shutdown callbacks after session is created
    ctx.add_shutdown_callback(log_usage)
    ctx.add_shutdown_callback(save_chat_history)

    # Start recording user's audio to Supabase storage
    # This records ONLY the user's audio (not the agent) and automatically stops when they leave
    recording_id = await start_room_recording(ctx.room.name, user_id, participant.identity)

    # Start avatar
    avatar = hedra.AvatarSession(
        avatar_id=avatar_id,  # Dynamically selected based on user's gender
#        aspect_ratio="9:16",
#        resolution="720p"
    )
    await avatar.start(session, room=ctx.room)

    # Start the session with the selected agent - this blocks and keeps the agent alive
    # The agent's on_enter() method will handle the initial greeting
    await session.start(
        room=ctx.room,
        agent=initial_agent,
    )        

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
