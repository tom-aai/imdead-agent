"""
Message data service for fetching student messages for the mail agent.
"""

import logging
from dataclasses import dataclass
from typing import Optional, List

from supabase import Client

logger = logging.getLogger(__name__)


@dataclass
class StudentMessage:
    """A message from a student to the expert."""
    id: str
    source_session_id: str
    chat_session_id: str
    student_user_id: str
    student_name: str
    student_company: Optional[str]
    student_role: Optional[str]
    message_content: str
    message_type: str  # "question" or "introduction_request"
    status: str
    created_at: str


class MessageDataService:
    """Service for fetching message data from Supabase for the mail agent."""

    def __init__(self, supabase_client: Optional[Client] = None):
        self.supabase = supabase_client
        self.logger = logging.getLogger(__name__)

    async def fetch_pending_messages(self, source_session_id: str) -> List[StudentMessage]:
        """
        Fetch all pending messages for a given source session.

        Args:
            source_session_id: The source interview session ID

        Returns:
            List of StudentMessage objects
        """
        if not self.supabase or not source_session_id:
            self.logger.warning("Cannot fetch messages - no client or session_id")
            return []

        try:
            response = self.supabase.table("student_messages").select(
                "id, source_session_id, chat_session_id, student_user_id, "
                "student_name, student_company, student_role, "
                "message_content, message_type, status, created_at"
            ).eq("source_session_id", source_session_id)\
             .eq("status", "pending")\
             .order("created_at", desc=False)\
             .execute()

            if not response.data:
                return []

            messages = []
            for row in response.data:
                messages.append(StudentMessage(
                    id=row.get("id"),
                    source_session_id=row.get("source_session_id"),
                    chat_session_id=row.get("chat_session_id"),
                    student_user_id=row.get("student_user_id"),
                    student_name=row.get("student_name", "Unknown"),
                    student_company=row.get("student_company"),
                    student_role=row.get("student_role"),
                    message_content=row.get("message_content", ""),
                    message_type=row.get("message_type", "question"),
                    status=row.get("status", "pending"),
                    created_at=row.get("created_at", "")
                ))

            self.logger.info(f"Fetched {len(messages)} pending messages for session {source_session_id}")
            return messages

        except Exception as e:
            self.logger.error(f"Error fetching messages for {source_session_id}: {e}")
            return []

    async def get_pending_message_count(self, source_session_id: str) -> int:
        """
        Get the count of pending messages for a session.

        Args:
            source_session_id: The source interview session ID

        Returns:
            Number of pending messages
        """
        if not self.supabase or not source_session_id:
            return 0

        try:
            response = self.supabase.table("student_messages").select(
                "id", count="exact"
            ).eq("source_session_id", source_session_id)\
             .eq("status", "pending")\
             .execute()

            return response.count or 0

        except Exception as e:
            self.logger.error(f"Error counting messages for {source_session_id}: {e}")
            return 0

    def update_message_status(
        self,
        message_id: str,
        status: str,
        expert_response: Optional[str] = None
    ) -> bool:
        """
        Update the status of a message.

        Args:
            message_id: The message ID
            status: New status (e.g., "read", "answered", "declined")
            expert_response: Optional response text for answered questions

        Returns:
            True if updated successfully
        """
        if not self.supabase or not message_id:
            return False

        try:
            update_data = {"status": status}
            if expert_response is not None:
                update_data["expert_response"] = expert_response
            if status in ("read", "answered"):
                from datetime import datetime, UTC
                update_data["read_at"] = datetime.now(UTC).isoformat()

            self.supabase.table("student_messages").update(
                update_data
            ).eq("id", message_id).execute()

            self.logger.info(f"Updated message {message_id} status to {status}")
            return True

        except Exception as e:
            self.logger.error(f"Error updating message {message_id}: {e}")
            return False


def format_messages_for_prompt(messages: List[StudentMessage]) -> str:
    """
    Format messages for inclusion in the mail agent prompt.

    Args:
        messages: List of StudentMessage objects

    Returns:
        Formatted string for the prompt
    """
    if not messages:
        return "No pending messages."

    lines = []
    for i, msg in enumerate(messages, 1):
        # Build context string
        context_parts = [msg.student_name]
        if msg.student_role:
            context_parts.append(msg.student_role)
        if msg.student_company:
            context_parts.append(f"at {msg.student_company}")
        context = ", ".join(context_parts)

        # Format message
        type_label = "Question" if msg.message_type == "question" else "Introduction Request"
        lines.append(f"**Message {i}** ({type_label}) - ID: {msg.id}")
        lines.append(f"From: {context}")
        lines.append(f"Message: {msg.message_content}")
        lines.append("")

    return "\n".join(lines)

