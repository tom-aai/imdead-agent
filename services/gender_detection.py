"""
Gender detection service using Anthropic's Claude API.
Determines likely gender based on first name to select appropriate avatar and voice.
"""

import logging
import os
from anthropic import Anthropic
from typing import Literal

logger = logging.getLogger(__name__)


class GenderDetector:
    """Service for detecting likely gender from first name."""
    
    def __init__(self, anthropic_api_key: str = None):
        """
        Initialize the gender detector.
        
        Args:
            anthropic_api_key: Anthropic API key (optional, will use env var if not provided)
        """
        self.anthropic = Anthropic(api_key=anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))
    
    def detect_gender(self, first_name: str) -> Literal["male", "female"]:
        """
        Detect likely gender from a first name using Claude Haiku 4.5.
        
        Args:
            first_name: The user's first name
            
        Returns:
            str: Either "male" or "female" (defaults to "male" if unclear)
        """
        if not first_name:
            logger.warning("No first name provided, defaulting to male")
            return "male"
        
        logger.info(f"Detecting gender for first name: {first_name}")
        
        prompt = f"""You are a gender detection system. Based on the first name provided, determine if the name is likely associated with a male or female person.

First name: {first_name}

Rules:
- Return "male" if the name is typically masculine
- Return "female" if the name is typically feminine
- If the name is ambiguous, gender-neutral, or you're unsure, default to "male"
- Only return the word "male" or "female" as your response

Your response:"""
        
        try:
            # Call Claude API with Haiku 4.5
            message = self.anthropic.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=10,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            # Extract the response
            response_text = message.content[0].text.strip().lower()
            
            # Validate response
            if "female" in response_text:
                gender = "female"
            else:
                # Default to male for any unclear response
                gender = "male"
            
            logger.info(f"Detected gender for '{first_name}': {gender}")
            return gender
            
        except Exception as e:
            logger.error(f"Error detecting gender: {e}")
            logger.warning("Defaulting to male due to error")
            return "male"

