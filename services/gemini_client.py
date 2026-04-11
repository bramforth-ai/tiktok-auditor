"""
TikTok Auditor - Gemini API Client
Generic wrapper for Google Gemini API calls.
Supports two model tiers (triage/smart) with retry logic.
"""

import os
import time
from google import genai


MAX_RETRIES = 3
BASE_DELAY = 2  # seconds


class GeminiClient:
    """Client for sending prompts to Google Gemini."""

    def __init__(self, api_key: str = None):
        """
        Initialize Gemini client.

        Args:
            api_key: Google Gemini API key. Uses GEMINI_API_KEY env var if not provided.
        """
        if api_key is None:
            api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not provided and not found in environment")

        self.client = genai.Client(api_key=api_key)
        self.delay = float(os.environ.get("GEMINI_DELAY_SECONDS", "1"))

        # Model names from environment
        self.triage_model = os.environ.get("GEMINI_TRIAGE_MODEL", "gemini-3-flash-preview")
        self.smart_model = os.environ.get("GEMINI_SMART_MODEL", "gemini-3.1-pro-preview")

    def call(self, model: str, prompt: str, json_mode: bool = False) -> str:
        """
        Send prompt to Gemini and return response text.

        Args:
            model: Model name (use self.triage_model or self.smart_model)
            prompt: Complete prompt string
            json_mode: If True, request JSON response format

        Returns:
            Response text string

        Raises:
            Exception: If all retries fail
        """
        config = {}
        if json_mode:
            config["response_mime_type"] = "application/json"

        # Rate limiting delay
        time.sleep(self.delay)

        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=[prompt],
                    config=config if config else None,
                )
                return response.text

            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                delay = BASE_DELAY * (2 ** attempt)  # 2s, 4s, 8s
                print(f"Gemini error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                print(f"Retrying in {delay}s...")
                time.sleep(delay)

    def call_triage(self, prompt: str, json_mode: bool = True) -> str:
        """
        Send prompt to the triage (cheap/fast) model.
        Defaults to JSON mode since triage outputs are structured.

        Args:
            prompt: Complete prompt string
            json_mode: Request JSON response (default True)

        Returns:
            Response text string
        """
        return self.call(self.triage_model, prompt, json_mode=json_mode)

    def call_smart(self, prompt: str, json_mode: bool = False) -> str:
        """
        Send prompt to the smart (expensive/capable) model.
        Defaults to prose mode since smart outputs are often reports/analysis.

        Args:
            prompt: Complete prompt string
            json_mode: Request JSON response (default False)

        Returns:
            Response text string
        """
        return self.call(self.smart_model, prompt, json_mode=json_mode)
