import json
import os
import re
from typing import Any, Dict, Optional

from dotenv import load_dotenv


try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


class GeminiClient:
    """
    Handles Gemini API communication and JSON extraction.

    This class keeps Gemini-specific code away from the main agent.
    """

    def __init__(
        self,
        use_llm: bool = True,
        model_name: Optional[str] = None,
    ):
        load_dotenv()

        self.use_llm = use_llm
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model_name = model_name or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

        self.client = None

        if self.is_available:
            self.client = genai.Client(api_key=self.api_key)

    @property
    def is_available(self) -> bool:
        return bool(self.use_llm and self.api_key and genai is not None)

    def call_json(self, prompt: str) -> str:
        """
        Calls Gemini with low temperature.

        First tries JSON mode. If the selected model does not support JSON MIME mode,
        it retries with normal generation.
        """

        if genai is None or types is None:
            raise RuntimeError("google-genai is not installed.")

        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is missing.")

        if self.client is None:
            self.client = genai.Client(api_key=self.api_key)

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
        except Exception:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                ),
            )

        response_text = getattr(response, "text", "")

        if not response_text:
            raise RuntimeError("Gemini returned an empty response.")

        return response_text

    def extract_json(self, raw_text: str) -> Dict[str, Any]:
        """
        Extracts JSON from Gemini output.

        Handles:
        - pure JSON
        - ```json fenced JSON
        - accidental surrounding text
        """

        cleaned = raw_text.strip()

        cleaned = re.sub(r"^```json", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^```", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError("No valid JSON object found in Gemini response.")

        json_text = cleaned[start : end + 1]
        return json.loads(json_text)