from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import pytest
from zonegate.agent.base import LLMClientProtocol
from zonegate.agent.context_evaluator import ContextEvaluator, ToolSelection
from zonegate.domain.decisions import ContextEvaluation, RecommendedControl
from zonegate.domain.evidence import CanonicalEvidence
from zonegate.domain.transactions import TransactionRequest
from zonegate.integrations.nokia.mcp import (
    NokiaMCPClient,
    NokiaMCPClientProtocol,
    UnauthorizedMCPToolError,
)


class FakeNokiaMCPClient:
    """Test double for Nokia Network as Code MCP client."""

    def __init__(
        self,
        tools: list[dict[str, Any]] | None = None,
        static_allowlist: set[str] | None = None,
    ) -> None:
        self.static_allowlist: set[str] = set(static_allowlist) if static_allowlist else set()
        self._tools = tools or [
            {
                "name": "get_device_location",
                "description": "Get network verified device location",
                "input_schema": {"type": "object", "properties": {"phoneNumber": {"type": "string"}}},
            },
            {
                "name": "check_roaming_status",
                "description": "Check if device is roaming",
                "input_schema": {"type": "object", "properties": {"phoneNumber": {"type": "string"}}},
            },
        ]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def is_tool_allowed(self, tool_name: str) -> bool:
        if not self.static_allowlist:
            return True
        return tool_name in self.static_allowlist

    async def list_tools(self) -> list[dict[str, Any]]:
        return [t for t in self._tools if self.is_tool_allowed(t["name"])]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if not self.is_tool_allowed(name):
            raise UnauthorizedMCPToolError(f"Tool '{name}' is not in the Nokia MCP static allowlist")
        self.calls.append((name, arguments or {}))
        return f'{{"status": "SUCCESS", "tool": "{name}", "data": {{"roaming": false}}}}'

    async def check_health(self) -> bool:
        return True


class FakeLLMClient:
    """Test double for LLMClientProtocol returning scripted responses."""

    provider_name: str = "fake"

    def __init__(self, tool_selection: ToolSelection | None = None) -> None:
        self.tool_selection = tool_selection or ToolSelection(call_tool=False)

    async def check_health(self) -> bool:
        return True

    async def generate_structured(
        self,
        prompt: str,
        response_model: Any,
        system_instruction: str = "",
    ) -> Any:
        if response_model == ToolSelection:
            return self.tool_selection
        if response_model == ContextEvaluation:
            return ContextEvaluation(
                risk_factors=["NONE"],
                recommended_control=RecommendedControl.APPROVE,
                rationale="Evaluated successfully with MCP telemetry",
            )
        raise ValueError(f"Unexpected model: {response_model}")


def test_empty_static_allowlist_permits_all_tools():
    # Empty allow list means every tool is eligible
    client = NokiaMCPClient(config_path=None, static_allowlist=set())
    assert client.is_tool_allowed("any_nokia_nac_tool") is True
    assert client.is_tool_allowed("get_device_location") is True


def test_non_empty_static_allowlist_restricts_tools():
    client = NokiaMCPClient(
        config_path=None,
        static_allowlist={"get_device_location"},
    )
    assert client.is_tool_allowed("get_device_location") is True
    assert client.is_tool_allowed("unauthorized_action") is False


@pytest.mark.asyncio
async def test_context_evaluator_calls_permitted_mcp_tool():
    mcp_double = FakeNokiaMCPClient(static_allowlist={"check_roaming_status"})
    llm_double = FakeLLMClient(
        tool_selection=ToolSelection(
            call_tool=True,
            tool_name="check_roaming_status",
            arguments={"phoneNumber": "+14155550199"},
            rationale="Check roaming anomaly",
        )
    )

    evaluator = ContextEvaluator(llm_client=llm_double, mcp_client=mcp_double)

    tx = TransactionRequest(
        transaction_id="tx_mcp_001",
        actor_id="actor_01",
        action="RELEASE_CARGO",
        resource_id="res_01",
        zone="ZONE_CARGO_BAY_1",
        timestamp=datetime.now(timezone.utc),
        value=Decimal("10000.00"),
    )
    evidence = CanonicalEvidence(
        number_verified=True,
        location_verified=True,
        collected_at=datetime.now(timezone.utc),
    )

    result = await evaluator.evaluate(tx, evidence)

    assert result.recommended_control == RecommendedControl.APPROVE
    assert len(mcp_double.calls) == 1
    assert mcp_double.calls[0][0] == "check_roaming_status"


@pytest.mark.asyncio
async def test_context_evaluator_rejects_unauthorized_mcp_tool_by_allowlist():
    # Only 'get_device_location' is in allowlist, but LLM attempts to call 'admin_wipe_device'
    mcp_double = FakeNokiaMCPClient(static_allowlist={"get_device_location"})
    llm_double = FakeLLMClient(
        tool_selection=ToolSelection(
            call_tool=True,
            tool_name="admin_wipe_device",
            arguments={},
            rationale="Attempt unauthorized call",
        )
    )

    evaluator = ContextEvaluator(llm_client=llm_double, mcp_client=mcp_double)

    tx = TransactionRequest(
        transaction_id="tx_mcp_002",
        actor_id="actor_01",
        action="RELEASE_CARGO",
        resource_id="res_01",
        zone="ZONE_CARGO_BAY_1",
        timestamp=datetime.now(timezone.utc),
        value=Decimal("10000.00"),
    )
    evidence = CanonicalEvidence(
        number_verified=True,
        location_verified=True,
        collected_at=datetime.now(timezone.utc),
    )

    # Tool call is rejected safely, evaluation finishes without unhandled crash
    result = await evaluator.evaluate(tx, evidence)
    assert result.recommended_control == RecommendedControl.APPROVE
    # Tool was NOT called on MCP server
    assert len(mcp_double.calls) == 0
