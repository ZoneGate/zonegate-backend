"""Tests for the local Ollama runtime client.

Ollama runs on the operator's own machine, so the failure modes are local ones:
the daemon is not running, or a cold model load outruns the timeout. These look
identical in a log unless the client says which happened -- and telling them
apart is the difference between "start Ollama" and "wait longer".

The rule that matters most is at the bottom: a runtime failure never becomes an
authorization outcome.
"""

import httpx
import pytest
from pydantic import BaseModel

from zonegate.agent.ollama import (
    OllamaClient,
    OllamaServiceUnavailableError,
    OllamaStructuredOutputError,
)


class Advice(BaseModel):
    verdict: str


def client_raising(exc: Exception) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama.test"
    )


def client_returning(payload: dict, status: int = 200) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama.test"
    )


def chat_reply(content: str) -> dict:
    return {"message": {"content": content}}


@pytest.mark.asyncio
async def test_timeout_names_the_timeout_not_a_connection_failure():
    api = OllamaClient(
        client=client_raising(httpx.TimeoutException("timed out")),
        base_url="http://host.docker.internal:11434",
        model="llama3.2",
        timeout=45.0,
    )

    with pytest.raises(OllamaServiceUnavailableError) as caught:
        await api.generate_structured(prompt="x", response_model=Advice)

    message = str(caught.value)
    assert "timed out after 45s" in message
    assert "llama3.2" in message


@pytest.mark.asyncio
async def test_a_refused_connection_is_reported_as_unreachable():
    api = OllamaClient(
        client=client_raising(httpx.ConnectError("refused")),
        base_url="http://127.0.0.1:11434",
    )

    with pytest.raises(OllamaServiceUnavailableError) as caught:
        await api.generate_structured(prompt="x", response_model=Advice)

    message = str(caught.value)
    assert "unavailable" in message
    assert "timed out" not in message


@pytest.mark.asyncio
async def test_the_timeout_default_survives_a_cold_model_load():
    # A first generation on CPU took ~30s in practice; the default has to clear
    # that with room to spare or every restart loses its first decision.
    assert OllamaClient().timeout >= 60.0


@pytest.mark.asyncio
async def test_health_is_false_when_the_daemon_is_down():
    api = OllamaClient(client=client_raising(httpx.ConnectError("refused")))

    assert await api.check_health() is False


@pytest.mark.asyncio
async def test_health_is_true_when_the_daemon_answers():
    api = OllamaClient(client=client_returning({"version": "0.34.0"}))

    assert await api.check_health() is True


@pytest.mark.asyncio
async def test_structured_output_is_validated_against_the_schema():
    api = OllamaClient(client=client_returning(chat_reply('{"verdict": "HOLD"}')))

    assert (await api.generate_structured(prompt="x", response_model=Advice)).verdict == "HOLD"


@pytest.mark.asyncio
async def test_an_empty_completion_is_an_error_not_an_empty_assessment():
    api = OllamaClient(client=client_returning(chat_reply("")))

    with pytest.raises(OllamaStructuredOutputError):
        await api.generate_structured(prompt="x", response_model=Advice, max_retries=0)


@pytest.mark.asyncio
async def test_unparseable_output_never_becomes_a_silent_default():
    # Left to itself, a model that answers in prose must not yield an Advice
    # with default fields -- the caller has to see the failure.
    api = OllamaClient(client=client_returning(chat_reply("Sure! Here is my advice.")))

    with pytest.raises(Exception) as caught:
        await api.generate_structured(prompt="x", response_model=Advice, max_retries=0)

    assert not isinstance(caught.value, OllamaServiceUnavailableError)
