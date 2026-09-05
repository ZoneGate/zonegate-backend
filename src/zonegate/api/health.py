from typing import Any
from litestar import get
from litestar.di import NamedDependency
from zonegate.agent.base import LLMClientProtocol
from zonegate.storage.zova import ZoneGateStore


@get("/health")
async def health_check(
    store: NamedDependency[ZoneGateStore],
    llm_client: NamedDependency[LLMClientProtocol],
) -> dict[str, Any]:
    """Health check distinguishing basic application status from external dependency reachability."""
    llm_ok = await llm_client.check_health()
    runtime_key = f"{getattr(llm_client, 'provider_name', 'ollama')}_agent_runtime"
    return {
        "status": "healthy",
        "dependencies": {
            "zova_persistence": "connected",
            runtime_key: "available" if llm_ok else "unavailable",
            "nokia_camara_gateway": "configured",
        },
    }
