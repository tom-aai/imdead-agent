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
from services.user_data import UserDataService, UserProfileData, build_session_type_context
from services.message_data import MessageDataService, format_messages_for_prompt

load_dotenv(".env.local")

logger = logging.getLogger("agent")

# Suppress noisy hpack (HTTP/2) debug logs
logging.getLogger("hpack").setLevel(logging.WARNING)

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

# Initialize message data service
message_service = MessageDataService(supabase)

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
        logger.warning("Supabase S3 credentials not found - skipping recording")
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
        """End the onboarding call. Call this tool when you have completed all 5 onboarding 
        objectives. The LLM should say goodbye in its response before calling this tool.
        """
        if not self.job_context:
            logger.error("No job context available for end_call")
            return None, "Failed to end call: No job context available"

        room_name = self.job_context.room.name
        logger.info(f"Ending onboarding call - waiting for playout before closing room {room_name}")

        # Wait for the LLM's farewell message to finish playing
        await context.wait_for_playout()

        logger.info("Farewell spoken, deleting room")

        # Delete the room — this triggers participant disconnect, which auto-closes
        # the session and fires shutdown callbacks (save_chat_history, etc.)
        try:
            await self.job_context.api.room.delete_room(
                api.DeleteRoomRequest(room=room_name)
            )
            logger.info(f"Successfully ended call and deleted room {room_name}")
        except Exception as e:
            logger.error(f"Error deleting room: {e}")

class InterviewAgent(Agent):
    """Agent for returning users going through interviews."""
    def __init__(
        self, 
        job_context=None, 
        agent_name: str = "Ashley", 
        session_state: dict = None,
        is_resume_session: bool = False,
        previous_conversation: str = None,
        chat_ctx=None
    ) -> None:
        self.job_context = job_context
        self.agent_name = agent_name
        self.session_state = session_state
        self.is_resume_session = is_resume_session
        self.previous_conversation = previous_conversation
        super().__init__(
            instructions="",  # Will be set dynamically in on_enter()
            chat_ctx=chat_ctx,  # Preserve chat context from previous agent
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
        
        # Inject session type context
        session_type_context = build_session_type_context(
            self.is_resume_session, 
            self.previous_conversation
        )
        personalized_prompt = personalized_prompt.replace(
            "[SESSION_TYPE_CONTEXT]",
            session_type_context
        )
        
        # Update the agent's instructions
        await self.update_instructions(personalized_prompt)

        # Generate appropriate greeting based on session type
        if self.is_resume_session:
            # Check for pending messages in resume sessions
            original_session_id = self.session_state.get("original_session_id") if self.session_state else None
            pending_count = 0
            if original_session_id:
                pending_count = await message_service.get_pending_message_count(original_session_id)
            
            if pending_count > 0:
                # Offer to check messages
                await self.session.generate_reply(
                    instructions=f"Welcome the user back warmly. Let them know they have {pending_count} new message{'s' if pending_count != 1 else ''} from seekers who chatted with their avatar. Ask if they'd like to check their messages first, or if they'd prefer to continue discussing their main topic. If they want to check messages, you have a check_messages tool available."
                )
            else:
                await self.session.generate_reply(
                    instructions="Welcome the user back warmly. Briefly acknowledge that you're continuing from your previous conversation. Ask what they'd like to explore further today."
                )
        else:
            await self.session.generate_reply(
                instructions="Greet the user and tell them the agenda for the call."
            )

    @function_tool
    async def check_messages(self, context: RunContext):
        """
        Check messages from seekers. Use this when the user wants to review 
        messages from seekers who chatted with their avatar.
        """
        # Get the original session ID from session state
        session_state = self.session_state or {}
        original_session_id = session_state.get("original_session_id")
        main_topic = session_state.get("main_topic", "your expertise")
        
        if not original_session_id:
            return "No session ID available to check messages."
        
        # Get pending messages
        messages = await message_service.fetch_pending_messages(original_session_id)
        
        if not messages:
            return "You don't have any pending messages from seekers right now."
        
        # Hand off to MailAgent by returning the new agent
        # The framework automatically handles the handoff when an Agent is returned
        mail_agent = MailAgent(
            job_context=self.job_context,
            agent_name=self.agent_name,
            session_state=self.session_state,
            messages=messages,
            original_session_id=original_session_id,
            main_topic=main_topic,
            chat_ctx=self.chat_ctx  # Preserve chat context
        )
        
        return mail_agent, f"Transferring to check {len(messages)} messages."


class MailAgent(Agent):
    """Agent for reviewing and responding to seeker messages."""
    
    def __init__(
        self,
        job_context=None,
        agent_name: str = "Ashley",
        session_state: dict = None,
        messages: list = None,
        original_session_id: str = None,
        main_topic: str = "your expertise",
        chat_ctx=None
    ) -> None:
        self.job_context = job_context
        self.agent_name = agent_name
        self.session_state = session_state or {}
        self.messages = messages or []
        self.original_session_id = original_session_id
        self.main_topic = main_topic
        self.current_message_index = 0
        
        super().__init__(
            instructions="",  # Will be set dynamically in on_enter()
            chat_ctx=chat_ctx,  # Preserve chat context from previous agent
        )
    
    async def on_enter(self) -> None:
        """Called when the mail agent is activated."""
        logger.info(f"MailAgent activated with {len(self.messages)} messages")
        
        # Load and personalize the mail prompt
        base_prompt = load_prompt('mail_prompt.txt')
        
        # Get expert name from session state
        expert_name = "the expert"  # Default
        if self.session_state:
            expert_name = self.session_state.get("user_name", "the expert")
        
        # Format messages for prompt
        messages_data = format_messages_for_prompt(self.messages)
        
        # Build personalized prompt
        personalized_prompt = base_prompt.replace(
            "[AGENT_NAME]", self.agent_name
        ).replace(
            "[EXPERT_NAME]", expert_name
        ).replace(
            "[MESSAGES_DATA]", messages_data
        ).replace(
            "[MAIN_TOPIC]", self.main_topic
        )
        
        await self.update_instructions(personalized_prompt)
        
        # Generate opening
        count = len(self.messages)
        await self.session.generate_reply(
            instructions=f"Tell the user they have {count} message{'s' if count != 1 else ''} from seekers to review. Then start presenting the first message."
        )
    
    @function_tool
    async def handle_message(
        self,
        context: RunContext,
        message_id: str,
        action: str,
        response: Optional[str] = None
    ) -> str:
        """
        Handle a seeker message with the specified action.
        
        Args:
            message_id: The ID of the message to handle
            action: The action to take - must be one of: 'answer', 'approve', 'decline', 'skip'
            response: The expert's answer (required only when action is 'answer')
        """
        action = action.lower().strip()
        
        if action == "answer":
            if not response:
                return "Please provide a response when answering a question."
            success = message_service.update_message_status(
                message_id=message_id,
                status="answered",
                expert_response=response
            )
            if success:
                logger.info(f"Recorded answer for message {message_id}")
                return "Answer recorded. The seeker will be notified and can access your response through your avatar."
            else:
                return "There was an issue recording the answer. Please try again."
        
        elif action == "approve":
            success = message_service.update_message_status(
                message_id=message_id,
                status="intro_approved"
            )
            if success:
                logger.info(f"Approved introduction for message {message_id}")
                return "Introduction approved. An email will be sent to connect you with the seeker."
            else:
                return "There was an issue approving the introduction. Please try again."
        
        elif action == "decline":
            success = message_service.update_message_status(
                message_id=message_id,
                status="declined"
            )
            if success:
                logger.info(f"Declined message {message_id}")
                return "Message declined. No notification will be sent."
            else:
                return "There was an issue declining the message. Please try again."
        
        elif action == "skip":
            logger.info(f"Skipped message {message_id}")
            return "Message skipped. You can come back to it later."
        
        else:
            return f"Unknown action '{action}'. Use 'answer', 'approve', 'decline', or 'skip'."
    
    @function_tool
    async def continue_interview(self, context: RunContext):
        """
        Transfer to the interview session to continue discussing the main topic.
        Use this when the expert wants to continue exploring their main topic after reviewing messages.
        """
        # Hand back to the InterviewAgent by returning the new agent
        # The framework automatically handles the handoff when an Agent is returned
        interview_agent = InterviewAgent(
            job_context=self.job_context,
            agent_name=self.agent_name,
            session_state=self.session_state,
            is_resume_session=True,
            previous_conversation=self.session_state.get("conversation_summary", ""),
            chat_ctx=self.chat_ctx  # Preserve chat context
        )
        
        return interview_agent, f"Transferring to continue discussing {self.main_topic}."

    @function_tool
    async def finish_mail_review(self, context: RunContext) -> str:
        """
        Complete the mail review session and end the call.
        Use this when the expert is done reviewing messages and wants to end the session.
        """
        logger.info("Mail review completed - triggering mail session orchestrator")
        
        # Trigger mail session orchestrator to process answered questions and approved intros
        if self.original_session_id and self.job_context:
            try:
                import aiohttp
                room_sid = await self.job_context.room.sid
                
                headers = {}
                backend_api_key = os.getenv("BACKEND_API_KEY")
                if backend_api_key:
                    headers["X-API-Key"] = backend_api_key
                
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.post(
                        f"{BACKEND_URL}/mail-session-orchestrator",
                        json={
                            "session_id": room_sid,
                            "original_session_id": self.original_session_id
                        },
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as response:
                        if response.status in (200, 202):
                            logger.info(f"✅ Mail session orchestrator triggered for {self.original_session_id}")
                        else:
                            logger.warning(f"⚠️ Mail session orchestrator returned status {response.status}")
            except Exception as e:
                logger.warning(f"⚠️ Could not trigger mail session orchestrator: {e}")
        
        return "Thank you for reviewing your messages. Goodbye!"


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
    is_resume_session = False
    original_session_id = None
    conversation_summary = None
    
    if participant.metadata:
        try:
            metadata = json.loads(participant.metadata)
            raw_picture_id = metadata.get("pictureId")
            user_info = metadata.get("user", {})
            
            # Check for resume session data
            is_resume_session = metadata.get("isResumeSession", False)
            original_session_id = metadata.get("originalSessionId")
            conversation_summary = metadata.get("conversationSummary")
            
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
            if is_resume_session:
                logger.info(f"🔄 RESUME SESSION: {original_session_id}")
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
    if detected_gender == "male":
        avatar_id = "bcd98623-d8ae-4078-93e4-89f2704e3e41"  # Ashley
        tts_voice = "inworld/inworld-tts-1:Ashley"  # Ashley's voice
        agent_name = "Ashley"
        logger.info(f"Male name detected - using Ashley avatar and Ashley voice")
    else:  # female
        avatar_id = "1e405274-e114-40f4-9cab-2f0a146df96d"  # Phil
        tts_voice = "inworld/inworld-tts-1:Edward"  # Phil's voice
        #tts_voice = "cartesia/sonic-3:6d6cc6bf-a98a-4db8-bd4e-43293bb49e1b"
        agent_name = "Phil"
        logger.info(f"Female name detected - using Phil avatar and male voice")
    
    # Select the appropriate agent based on user status
    # Use a dict so it can be mutated by agents during transfers
    session_state = {"call_type": None, "original_session_id": original_session_id}
    
    if is_resume_session:
        # Resume session always uses InterviewAgent with previous context
        logger.info(f"🔄 Resume session detected - using InterviewAgent with previous context")
        initial_agent = InterviewAgent(
            job_context=ctx, 
            agent_name=agent_name, 
            session_state=session_state,
            is_resume_session=True,
            previous_conversation=conversation_summary
        )
        session_state["call_type"] = "resume"
    elif is_returning_user:
        logger.info(f"Returning user detected - using InterviewAgent")
        initial_agent = InterviewAgent(job_context=ctx, agent_name=agent_name, session_state=session_state)
        session_state["call_type"] = "interview"
    else:
        logger.info(f"New user detected - using OnboardingAgent")
        initial_agent = OnboardingAgent(job_context=ctx, agent_name=agent_name)
        session_state["call_type"] = "onboarding"
    
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
        
        # Always save and trigger backend, even if user never spoke.
        # The backend orchestrator will detect empty transcripts and clean up appropriately.
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
            user_info=user_info,
            picture_id=picture_id,
            conversation_data=history_data,
            usage_data=usage_data,
            usage_summary=usage_summary,
            backend_url=BACKEND_URL,
            recording_id=recording_id,
            call_type=session_state["call_type"],
            backend_api_key=BACKEND_API_KEY,
            original_session_id=session_state.get("original_session_id")
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

    # Start recording user's audio to Supabase storage (skip for resume sessions - we already have their voice)
    # This records ONLY the user's audio (not the agent) and automatically stops when they leave
    recording_id = None
    if not is_resume_session:
        recording_id = await start_room_recording(ctx.room.name, user_id, participant.identity)
    else:
        logger.info("⏭️ Skipping voice recording for resume session")

    # Start avatar
    avatar = hedra.AvatarSession(
        avatar_id=avatar_id
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
