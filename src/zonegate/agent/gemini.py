import logging
from typing import Type, TypeVar
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class GeminiClientError(Exception):
    """Base exception for Gemini interactions."""


class GeminiClient:
    """Structured-output client using official google-genai SDK."""

    provider_name: str = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self._client = genai.Client(api_key=self.api_key) if self.api_key else None

    async def check_health(self) -> bool:
        """Returns True if Gemini API key is configured and can query the model."""
        if not self._client or not self.api_key:
            return False
        try:
            await self._client.aio.models.get(model=self.model)
            return True
        except Exception:
            return False

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_instruction: str = "You are a precise security advisory AI. Output only valid JSON matching the schema.",
    ) -> T:
        if not self._client:
            raise GeminiClientError("Gemini API key is not configured")

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=response_model,
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            if response.parsed and isinstance(response.parsed, response_model):
                return response.parsed
            if response.text:
                return response_model.model_validate_json(response.text)
            raise GeminiClientError("Gemini returned empty response")
        except APIError as exc:
            logger.error("Gemini API error: %s", exc)
            raise GeminiClientError(f"Gemini API request failed: {exc}") from exc
        except (ValidationError, Exception) as exc:
            logger.error("Gemini structured output parsing error: %s", exc)
            raise GeminiClientError(f"Failed to parse Gemini output into schema: {exc}") from exc
