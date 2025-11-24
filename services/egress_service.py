"""
Egress service for recording interview sessions.
Handles starting and managing LiveKit egress to record audio/video to Supabase storage.
"""

import logging
import os
from typing import Optional, Any
from livekit import api

logger = logging.getLogger(__name__)


class EgressService:
    """Service for managing LiveKit egress recordings to Supabase storage."""
    
    def __init__(self):
        """Initialize the egress service."""
        # Get LiveKit credentials from environment
        self.livekit_url = os.getenv("LIVEKIT_URL")
        self.livekit_api_key = os.getenv("LIVEKIT_API_KEY")
        self.livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")
        
        # Get Supabase storage S3 credentials
        # Note: These are separate from your Supabase API credentials
        # Generate them in: Supabase Dashboard -> Project Settings -> Storage -> S3 Access Keys
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_project_id = self._extract_project_id(self.supabase_url)
        self.s3_access_key_id = os.getenv("SUPABASE_S3_ACCESS_KEY_ID")
        self.s3_secret_access_key = os.getenv("SUPABASE_S3_SECRET_ACCESS_KEY")
        self.s3_region = os.getenv("SUPABASE_S3_REGION", "auto")
        
        self.logger = logging.getLogger(__name__)
        
        # LiveKit API client will be lazily initialized when needed
        self._lkapi = None
    
    def _extract_project_id(self, supabase_url: Optional[str]) -> Optional[str]:
        """Extract project ID from Supabase URL."""
        if not supabase_url:
            return None
        # Supabase URL format: https://<project-id>.supabase.co
        try:
            return supabase_url.split("//")[1].split(".")[0]
        except (IndexError, AttributeError):
            self.logger.warning(f"Could not extract project ID from Supabase URL: {supabase_url}")
            return None
    
    def _get_lkapi(self) -> Optional[api.LiveKitAPI]:
        """Lazily initialize and return the LiveKit API client."""
        if self._lkapi is None:
            if self.livekit_url and self.livekit_api_key and self.livekit_api_secret:
                self._lkapi = api.LiveKitAPI(
                    url=self.livekit_url,
                    api_key=self.livekit_api_key,
                    api_secret=self.livekit_api_secret
                )
                self.logger.info("✅ LiveKit API client initialized for egress")
            else:
                self.logger.warning("⚠️ LiveKit credentials not found - egress disabled")
                return None
        return self._lkapi
    
    async def start_room_recording(
        self,
        room_name: str,
        user_id: str,
        audio_only: bool = True,
        audio_mixing: Optional[api.AudioMixing] = None,
        preset: api.EncodingOptionsPreset = api.EncodingOptionsPreset.H264_720P_30
    ) -> Optional[str]:
        """
        Start recording a room to Supabase storage.
        
        Args:
            room_name: Name of the LiveKit room to record
            user_id: User ID for organizing recordings in storage
            audio_only: If True, record audio only. If False, record audio + video.
            audio_mixing: Audio mixing mode for dual-channel recording (only for audio_only).
                         Use api.AudioMixing.DUAL_CHANNEL_AGENT to separate agent/user audio.
            preset: Encoding preset for the recording (only used if audio_only=False)
            
        Returns:
            egress_id: The egress ID if successful, None otherwise
        """
        lkapi = self._get_lkapi()
        if not lkapi:
            self.logger.warning("Cannot start recording - LiveKit API not initialized")
            return None
        
        if not self.supabase_url or not self.s3_access_key_id or not self.s3_secret_access_key:
            self.logger.warning("Cannot start recording - Supabase S3 credentials not found")
            self.logger.warning("Please set SUPABASE_S3_ACCESS_KEY_ID and SUPABASE_S3_SECRET_ACCESS_KEY")
            return None
        
        try:
            # Construct the S3-compatible endpoint for Supabase storage
            # Format: https://<project-id>.storage.supabase.co/storage/v1/s3
            if not self.supabase_project_id:
                self.logger.error("Cannot construct storage endpoint - project ID not found")
                return None
            storage_endpoint = f"https://{self.supabase_project_id}.storage.supabase.co/storage/v1/s3"

            # Determine file extension based on audio_only setting
            file_extension = "ogg" if audio_only else "mp4"
            
            # Create filename with user_id and timestamp
            filename = f"interviews/{user_id}/{room_name}.{file_extension}"
            
            # Log S3 configuration (without sensitive data)
            self.logger.info(f"   S3 Endpoint: {storage_endpoint}")
            self.logger.info(f"   S3 Bucket: interview-recordings")
            self.logger.info(f"   S3 Region: {self.s3_region}")
            self.logger.info(f"   S3 Access Key ID: {self.s3_access_key_id[:8] if self.s3_access_key_id else '(empty)'}...")
            self.logger.info(f"   S3 Secret Access Key: {'*' * 10 if self.s3_secret_access_key else '(empty)'}...")
            self.logger.info(f"   Force Path Style: True")
            
            # Create the egress request for room composite
            req = api.RoomCompositeEgressRequest(
                room_name=room_name,
                layout="speaker",  # Use speaker layout (shows active speaker)
                audio_only=audio_only,
                audio_mixing=audio_mixing,  # Dual-channel recording (agent/user on separate channels)
                preset=None if audio_only else preset,
                file_outputs=[
                    api.EncodedFileOutput(
                        filepath=filename,
                        s3=api.S3Upload(
                            # Supabase Storage S3-compatible configuration
                            endpoint=storage_endpoint,
                            bucket="interview-recordings",
                            region=self.s3_region,
                            access_key=self.s3_access_key_id,
                            secret=self.s3_secret_access_key,
                            force_path_style=True,
                        ),
                    )
                ],
            )
            
            # Start the egress
            self.logger.info("=" * 50)
            self.logger.info("🎥 STARTING ROOM RECORDING")
            self.logger.info("=" * 50)
            self.logger.info(f"  Room: {room_name}")
            self.logger.info(f"  User ID: {user_id}")
            self.logger.info(f"  Audio Only: {audio_only}")
            if audio_mixing:
                mixing_name = {
                    api.AudioMixing.DEFAULT_MIXING: "DEFAULT (mixed)",
                    api.AudioMixing.DUAL_CHANNEL_AGENT: "DUAL_CHANNEL_AGENT (L=agent, R=user)",
                    api.AudioMixing.DUAL_CHANNEL_ALTERNATE: "DUAL_CHANNEL_ALTERNATE",
                }.get(audio_mixing, str(audio_mixing))
                self.logger.info(f"  Audio Mixing: {mixing_name}")
            self.logger.info(f"  Destination: interview-recordings/{filename}")
            self.logger.info("=" * 50)
            
            response = await lkapi.egress.start_room_composite_egress(req)
            egress_id = response.egress_id
            
            # Log detailed response information
            self.logger.info(f"✅ Recording started successfully - Egress ID: {egress_id}")
            self.logger.info(f"   Egress Status: {response.status}")
            self.logger.info(f"   Room ID: {response.room_id}")
            self.logger.info(f"   Started At: {response.started_at}")
            
            # Log file output configuration
            if response.file_results:
                for file_result in response.file_results:
                    self.logger.info(f"   File Result: {file_result}")
            
            # Log any error details if present
            if response.error:
                self.logger.error(f"   ⚠️ Egress Error: {response.error}")
            if response.details:
                self.logger.info(f"   Details: {response.details}")
            
            return egress_id
            
        except Exception as e:
            self.logger.error(f"❌ Error starting room recording: {e}")
            self.logger.exception("Full traceback:")
            return None
    
    async def get_egress_status(self, egress_id: str) -> Optional[Any]:
        """
        Get the status of an egress recording.
        
        Args:
            egress_id: The egress ID to check
            
        Returns:
            EgressInfo object if successful, None otherwise
        """
        lkapi = self._get_lkapi()
        if not lkapi:
            self.logger.warning("Cannot get egress status - LiveKit API not initialized")
            return None
        
        try:
            # List all egress with this ID
            request = api.ListEgressRequest(egress_id=egress_id)
            response = await lkapi.egress.list_egress(request)
            if response.items:
                egress_info = response.items[0]
                self.logger.info(f"📊 Egress Status for {egress_id}:")
                self.logger.info(f"   Status: {egress_info.status}")
                self.logger.info(f"   Started: {egress_info.started_at}")
                self.logger.info(f"   Ended: {egress_info.ended_at}")
                if egress_info.error:
                    self.logger.error(f"   ❌ Error: {egress_info.error}")
                if egress_info.file_results:
                    for idx, file_result in enumerate(egress_info.file_results):
                        self.logger.info(f"   File {idx+1}: {file_result.location}")
                        self.logger.info(f"   Size: {file_result.size} bytes")
                        self.logger.info(f"   Duration: {file_result.duration}s")
                return egress_info
            else:
                self.logger.warning(f"No egress found with ID: {egress_id}")
                return None
        except Exception as e:
            self.logger.error(f"❌ Error getting egress status: {e}")
            self.logger.exception("Full traceback:")
            return None
    
    async def stop_recording(self, egress_id: str) -> bool:
        """
        Stop an active egress recording.
        
        Args:
            egress_id: The egress ID to stop
            
        Returns:
            True if stopped successfully, False otherwise
        """
        lkapi = self._get_lkapi()
        if not lkapi:
            self.logger.warning("Cannot stop recording - LiveKit API not initialized")
            return False
        
        try:
            request = api.StopEgressRequest(egress_id=egress_id)
            await lkapi.egress.stop_egress(request)
            self.logger.info(f"✅ Recording stopped - Egress ID: {egress_id}")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error stopping recording {egress_id}: {e}")
            self.logger.exception("Full traceback:")
            return False
    
    async def stop_stuck_egress(self, egress_id: str) -> bool:
        """
        Force stop a stuck egress recording.
        
        Args:
            egress_id: The egress ID to stop
            
        Returns:
            True if stopped successfully, False otherwise
        """
        self.logger.warning(f"⚠️ Stopping stuck egress: {egress_id}")
        return await self.stop_recording(egress_id)
    
    async def close(self, egress_id: Optional[str] = None):
        """
        Close the LiveKit API client.
        
        Args:
            egress_id: Optional egress ID to check status before closing
        """
        # Check final egress status if provided
        if egress_id and self._lkapi:
            self.logger.info(f"Checking final status for egress {egress_id}...")
            await self.get_egress_status(egress_id)
        
        # Close the API client
        if self._lkapi:
            await self._lkapi.aclose()
            self.logger.info("LiveKit API client closed")

