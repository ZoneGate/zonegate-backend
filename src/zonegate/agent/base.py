from typing import Protocol, Type, TypeVar, runtime_checkable
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLMClientProtocol(Protocol):
    """Common runtime-checkable protocol for LLM runtimes (Ollama, Gemini)."""

    provider_name: str

    async def check_health(self) -> bool:
        ...

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_instruction: str = ...,
    ) -> T:
        ...
