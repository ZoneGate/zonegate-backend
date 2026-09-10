import logging
from typing import Type, TypeVar
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# Keys Pydantic emits that the Gemini `response_schema` (OpenAPI 3.0 subset) rejects.
# `additionalProperties` in particular is produced by every model using
# `ConfigDict(extra="forbid")` and makes the API reject the request outright.
_UNSUPPORTED_SCHEMA_KEYS = frozenset({"additionalProperties", "title", "$schema", "default", "examples"})


def to_gemini_schema(model: Type[BaseModel]) -> dict:
    """Converts a Pydantic model's JSON schema into the subset Gemini accepts.

    Inlines `$ref`/`$defs`, drops unsupported keywords, and rewrites `Optional[X]`
    unions (`anyOf` with a null branch) into a `nullable` field.
    """
    root = model.model_json_schema()
    defs = root.pop("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            overrides = {k: v for k, v in node.items() if k != "$ref"}
            return walk({**defs.get(name, {}), **overrides})

        if "anyOf" in node:
            variants = [v for v in node["anyOf"] if v.get("type") != "null"]
            nullable = len(variants) != len(node["anyOf"])
            rest = {k: v for k, v in node.items() if k != "anyOf" and k not in _UNSUPPORTED_SCHEMA_KEYS}
            if len(variants) == 1:
                resolved = walk({**variants[0], **rest})
            else:
                resolved = {k: walk(v) for k, v in rest.items()}
                resolved["anyOf"] = [walk(v) for v in variants]
            if nullable:
                resolved["nullable"] = True
            return resolved

        return {k: walk(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}

    return walk(root)


class GeminiClientError(Exception):
    """Base exception for Gemini interactions."""


class GeminiClient:
    """Structured-output client using official google-genai SDK."""

    provider_name: str = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.6-flash",
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
            response_schema=to_gemini_schema(response_model),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            if isinstance(response.parsed, response_model):
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
