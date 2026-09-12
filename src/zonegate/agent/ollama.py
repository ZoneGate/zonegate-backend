import json
import logging
from typing import Type, TypeVar
import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OllamaClientError(Exception):
    """Base exception for Ollama interactions."""


class OllamaServiceUnavailableError(OllamaClientError):
    """Raised when Ollama is unreachable, timed out, or connection refused."""


class OllamaStructuredOutputError(OllamaClientError):
    """Raised when model returns malformed JSON or invalid schema."""


class OllamaClient:
    """Lightweight structured-output client for local Ollama runtime.

    Enforces Pydantic model validation and safe failure modes.
    Never produces implicit authorization outcomes upon failure.
    """

    provider_name: str = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)

    async def check_health(self) -> bool:
        """Returns True if Ollama service is reachable and responsive."""
        try:
            resp = await self._client.get("/api/version", timeout=2.0)
            return resp.status_code == 200
        except (httpx.RequestError, Exception):
            return False

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_instruction: str = "You are a precise security advisory AI. Output only valid JSON matching the requested schema.",
        max_retries: int = 1,
    ) -> T:
        """Generates structured output strictly conforming to the given Pydantic model.

        Includes a single bounded retry for transient format violations.
        """
        schema = response_model.model_json_schema()
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema,
        }

        attempts = 0
        last_err = None

        while attempts <= max_retries:
            attempts += 1
            try:
                resp = await self._client.post("/api/chat", json=payload)
                if resp.status_code != 200:
                    raise OllamaClientError(f"Ollama returned HTTP {resp.status_code}: {resp.text}")
                data = resp.json()
                content = data.get("message", {}).get("content", "")
                if not content:
                    raise OllamaStructuredOutputError("Ollama response contained empty content")
                return response_model.model_validate_json(content)
            except httpx.TimeoutException as exc:
                logger.error(
                    "Ollama timed out after %.0fs at %s (model '%s'); a cold model load "
                    "can exceed this - raise OLLAMA_TIMEOUT if it recurs",
                    self.timeout, self.base_url, self.model,
                )
                raise OllamaServiceUnavailableError(
                    f"Ollama timed out after {self.timeout:.0f}s at {self.base_url} "
                    f"(model '{self.model}')"
                ) from exc
            except httpx.ConnectError as exc:
                logger.error("Ollama unreachable at %s: %s", self.base_url, exc)
                raise OllamaServiceUnavailableError(f"Ollama service unavailable at {self.base_url}: {exc}") from exc
            except (ValidationError, json.JSONDecodeError, OllamaStructuredOutputError) as exc:
                last_err = exc
                logger.warning("Ollama output parsing attempt %d failed: %s", attempts, exc)
                if attempts <= max_retries:
                    # Provide formatting feedback for the bounded retry
                    payload["messages"].append({"role": "assistant", "content": str(content if 'content' in locals() else "")})
                    payload["messages"].append({
                        "role": "user",
                        "content": f"The previous output failed schema validation: {exc}. Provide ONLY valid JSON matching the schema.",
                    })
                    continue

        raise OllamaStructuredOutputError(f"Failed to produce valid structured output after {attempts} attempts: {last_err}") from last_err
