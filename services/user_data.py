"""
User data service for managing user profiles and interview call history.
Handles all Supabase interactions for user-related data.
"""

import aiohttp
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass

from supabase import Client

logger = logging.getLogger(__name__)


def build_session_type_context(is_resume_session: bool, previous_conversation: Optional[str] = None) -> str:
    """
    Build the session type context for injection into the interview prompt.
    
    Args:
        is_resume_session: Whether this is a resumed session
        previous_conversation: Summary of the previous conversation (for resume sessions)
        
    Returns:
        Formatted context string for the prompt
    """
    if is_resume_session and previous_conversation:
        logger.info("Building resumed session context")
        return f"""**This is a RESUMED SESSION.** The user has returned to continue a previous conversation.

Here is a summary of what was discussed in the previous conversation:

{previous_conversation}"""
    else:
        return """**This is a NEW SESSION.** Treat this as a completely fresh conversation."""


@dataclass
class UserProfileData:
    """Store user profile data for personalized prompts."""
    myers_briggs_code: Optional[str] = None
    personality: Optional[str] = None
    expertise_description: Optional[str] = None


class UserDataService:
    """Service for managing user data and interview call history in Supabase."""
    
    def __init__(self, supabase_client: Optional[Client] = None):
        """
        Initialize the user data service.
        
        Args:
            supabase_client: Supabase client instance (optional)
        """
        self.supabase = supabase_client
        self.logger = logging.getLogger(__name__)
    
    async def check_if_returning_user(self, user_id: str) -> bool:
        """
        Check if a user has completed onboarding by looking for previous interview calls.
        
        Args:
            user_id: The user's ID
            
        Returns:
            bool: True if user has at least one completed interview call in the database
        """
        if not self.supabase or not user_id:
            self.logger.warning("Cannot check returning user status - no Supabase client or user_id")
            return False
        
        try:
            # Query for any previous interview calls for this user
            response = self.supabase.table("interview_calls").select("session_id").eq("user_id", user_id).limit(1).execute()
            
            has_previous_calls = len(response.data) > 0
            self.logger.info(f"User {user_id} returning status: {has_previous_calls}")
            return has_previous_calls
        except Exception as e:
            self.logger.error(f"Error checking returning user status: {e}")
            return False
    
    async def fetch_user_profile(self, user_id: str) -> Optional[UserProfileData]:
        """
        Fetch user profile data from Supabase for personalized prompts.
        
        Args:
            user_id: The user's ID
            
        Returns:
            UserProfileData with myers_briggs_code, personality, and expertise_description, or None
        """
        if not self.supabase or not user_id:
            self.logger.warning("Cannot fetch user profile - no Supabase client or user_id")
            return None
        
        try:
            # Query user_profiles table for this user
            response = self.supabase.table("user_profiles").select(
                "myers_briggs_code, personality, expertise_description"
            ).eq("user_id", user_id).limit(1).execute()
            
            if response.data and len(response.data) > 0:
                profile_data = response.data[0]
                user_profile = UserProfileData(
                    myers_briggs_code=profile_data.get("myers_briggs_code"),
                    personality=profile_data.get("personality"),
                    expertise_description=profile_data.get("expertise_description")
                )
                self.logger.info(f"✅ Fetched user profile for user {user_id}")
                self.logger.info(f"   Myers-Briggs: {user_profile.myers_briggs_code or 'None'}")
                self.logger.info(f"   Personality: {user_profile.personality[:50] if user_profile.personality else 'None'}...")
                self.logger.info(f"   Expertise: {user_profile.expertise_description[:50] if user_profile.expertise_description else 'None'}...")
                return user_profile
            else:
                self.logger.info(f"No user profile found for user {user_id}")
                return None
        except Exception as e:
            self.logger.error(f"Error fetching user profile: {e}")
            return None
    
    async def save_interview_call(
        self,
        session_id: str,
        job_id: str,
        user_id: str,
        picture_id: str,
        user_firstname: str,
        user_lastname: str,
        timestamp: str,
        usage_data: dict,
        conversation_data: dict,
        recording_id: Optional[str] = None,
        call_type: str = "interview"
    ) -> bool:
        """
        Save interview call data to Supabase.
        
        Args:
            session_id: LiveKit session/room SID
            job_id: LiveKit job ID
            user_id: User's ID
            picture_id: Picture ID for avatar
            user_firstname: User's first name
            user_lastname: User's last name
            timestamp: Timestamp string
            usage_data: Usage metrics dictionary
            conversation_data: Conversation history dictionary
            recording_id: UUID of the recording file (optional)
            call_type: Type of call - 'onboarding' or 'interview' (default: 'interview')
            
        Returns:
            bool: True if save was successful, False otherwise
        """
        if not self.supabase or not user_id:
            self.logger.warning("Cannot save to Supabase - no client or user_id")
            return False
        
        try:
            # Prepare data for Supabase (matches your table schema)
            supabase_data = {
                "session_id": session_id,
                "job_id": job_id,
                "picture_id": picture_id or '',
                "user_id": user_id,
                "user_firstname": user_firstname or '',
                "user_lastname": user_lastname or '',
                "timestamp": timestamp,
                "usage": usage_data,
                "conversation": conversation_data,
                "recording_id": recording_id,
                "call_type": call_type
            }
            
            # Insert into Supabase
            response = self.supabase.table("interview_calls").insert(supabase_data).execute()
            
            self.logger.info("=" * 50)
            self.logger.info("✅ DATA SAVED TO SUPABASE")
            self.logger.info("=" * 50)
            self.logger.info(f"  Session ID: {session_id}")
            self.logger.info(f"  Job ID: {job_id}")
            self.logger.info(f"  User: {user_firstname} {user_lastname} ({user_id})")
            self.logger.info(f"  Picture ID: {picture_id or 'N/A'}")
            self.logger.info(f"  Recording ID: {recording_id or 'N/A'}")
            self.logger.info(f"  Call Type: {call_type}")
            self.logger.info(f"  Conversation items: {len(conversation_data.get('items', []))}")
            self.logger.info("=" * 50)
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Error saving to Supabase: {e}")
            return False
    
    async def save_avatar_image_reference(
        self,
        user_id: str,
        picture_id: str
    ) -> bool:
        """
        Save user avatar image reference to Supabase.
        
        Args:
            user_id: User's ID
            picture_id: Picture ID for avatar
            
        Returns:
            bool: True if save was successful, False otherwise
        """
        if not self.supabase or not user_id or not picture_id:
            self.logger.warning("Cannot save avatar reference - missing client, user_id, or picture_id")
            return False
        
        try:
            avatar_image_data = {
                "user_id": user_id,
                "picture_id": picture_id,
                "picture_source": "from_interview_session"
            }
            self.supabase.table("user_avatar_images").insert(avatar_image_data).execute()
            self.logger.info(f"✅ Avatar image reference saved for user {user_id}: {picture_id}")
            return True
        except Exception as e:
            self.logger.warning(f"⚠️ Could not save avatar image reference: {e}")
            return False
    
    async def save_chat_history(
        self,
        session_id: str,
        job_id: str,
        user_info: Dict[str, Any],
        picture_id: Optional[str],
        conversation_data: dict,
        usage_data: dict,
        usage_summary: Any,
        backend_url: str,
        recording_id: Optional[str] = None,
        call_type: str = "interview",
        backend_api_key: Optional[str] = None,
        original_session_id: Optional[str] = None
    ) -> None:
        """
        Save complete chat history to Supabase.
        
        Args:
            session_id: LiveKit session/room SID
            job_id: LiveKit job ID
            user_info: Dictionary containing user information
            picture_id: Picture ID for avatar (optional)
            conversation_data: Conversation history dictionary
            usage_data: Usage metrics dictionary
            usage_summary: Usage summary object with metrics
            backend_url: Backend URL for post-call orchestrator
            recording_id: UUID of the recording file (optional)
            call_type: Type of call - 'onboarding', 'interview', or 'resume' (default: 'interview')
            backend_api_key: API key for authenticating with the backend (optional)
            original_session_id: For resume sessions, the original session ID to append to (optional)
        """
        try:
            current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
            user_id = user_info.get('userId', '')
            
            # Save to Supabase database
            if user_id:
                save_success = await self.save_interview_call(
                    session_id=session_id,
                    job_id=job_id,
                    user_id=user_id,
                    picture_id=picture_id or '',
                    user_firstname=user_info.get('firstName') or '',
                    user_lastname=user_info.get('lastName') or '',
                    timestamp=current_time,
                    usage_data=usage_data,
                    conversation_data=conversation_data,
                    recording_id=recording_id,
                    call_type=call_type
                )
                
                if save_success:
                    # Log additional metrics
                    self.logger.info(f"  LLM tokens: {usage_summary.llm_prompt_tokens} + {usage_summary.llm_completion_tokens}")
                    self.logger.info(f"  Audio duration: TTS={usage_summary.tts_audio_duration:.1f}s, STT={usage_summary.stt_audio_duration:.1f}s")
                    
                    # Save avatar image reference if we have picture_id
                    if picture_id:
                        await self.save_avatar_image_reference(user_id, picture_id)
                    
                    # Trigger appropriate orchestrator (fire and forget)
                    try:
                        headers = {}
                        if backend_api_key:
                            headers["X-API-Key"] = backend_api_key
                        
                        async with aiohttp.ClientSession() as http_session:
                            # Choose endpoint based on whether this is a resume session
                            if original_session_id:
                                # Resume session - append to existing session
                                endpoint = f"{backend_url}/resume-session-orchestrator"
                                payload = {
                                    "session_id": session_id,
                                    "original_session_id": original_session_id
                                }
                                self.logger.info(f"🔄 Triggering resume session orchestrator for {original_session_id}")
                            else:
                                # Regular session - full post-call processing
                                endpoint = f"{backend_url}/post-call-orchestrator"
                                payload = {"session_id": session_id}
                            
                            async with http_session.post(
                                endpoint,
                                json=payload,
                                headers=headers,
                                timeout=aiohttp.ClientTimeout(total=5)
                            ) as response:
                                if response.status in (200, 202):
                                    if original_session_id:
                                        self.logger.info(f"✅ Resume session orchestrator triggered for session {original_session_id}")
                                    else:
                                        self.logger.info(f"✅ Post-call orchestrator triggered for session {session_id}")
                                else:
                                    self.logger.warning(f"⚠️ Orchestrator returned status {response.status}")
                    except Exception as backend_error:
                        self.logger.warning(f"⚠️ Could not trigger orchestrator: {backend_error}")
                        self.logger.info("💾 Transcript is saved in Supabase and can be processed manually if needed")
            else:
                self.logger.warning("⚠️ No user_id available - skipping Supabase upload")
                
        except Exception as e:
            self.logger.error(f"❌ Error in save_chat_history: {e}")

