import pytest
from litestar.testing import AsyncTestClient
from zonegate.agent.gemini import GeminiClient, GeminiClientError
from zonegate.app import create_app
from zonegate.config import Settings
from zonegate.domain.evidence import EvidencePlan


@pytest.mark.asyncio
async def test_gemini_client_unconfigured_fails_safely():
    client = GeminiClient(api_key="")
    assert await client.check_health() is False

    with pytest.raises(GeminiClientError) as exc_info:
        await client.generate_structured(
            prompt="Plan evidence",
            response_model=EvidencePlan,
        )
    assert "Gemini API key is not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_health_reports_gemini_provider_when_configured():
    test_settings = Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY="",
        NOKIA_BASE_URL="http://127.0.0.1:59998",
    )
    app = create_app(test_settings)

    async with AsyncTestClient(app=app) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dependencies"]["gemini_agent_runtime"] == "unavailable"
