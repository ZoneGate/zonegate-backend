import pytest
from litestar.testing import AsyncTestClient
from zonegate.app import create_app
from zonegate.config import Settings


@pytest.mark.asyncio
async def test_health_endpoint_boots_without_live_dependencies():
    # Configure in-memory database and non-existent external ports
    test_settings = Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
        OLLAMA_BASE_URL="http://127.0.0.1:59999",  # non-existent port
        NOKIA_BASE_URL="http://127.0.0.1:59998",
    )
    app = create_app(test_settings)

    async with AsyncTestClient(app=app) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["dependencies"]["zova_persistence"] == "connected"
        assert data["dependencies"]["ollama_agent_runtime"] == "unavailable"
        assert data["dependencies"]["nokia_camara_gateway"] == "configured"
