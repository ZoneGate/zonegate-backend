import json
import logging
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from zonegate.agent.base import LLMClientProtocol
from zonegate.domain.decisions import ContextEvaluation
from zonegate.domain.evidence import CanonicalEvidence
from zonegate.domain.transactions import TransactionRequest
from zonegate.integrations.nokia.mcp import NokiaMCPClientProtocol, UnauthorizedMCPToolError

logger = logging.getLogger(__name__)


class ContextEvaluationError(Exception):
    """Raised when context evaluation fails."""


class ToolSelection(BaseModel):
    """Advisory tool selection request by the Context Evaluator."""
    model_config = ConfigDict(extra="forbid")

    call_tool: bool = Field(default=False, description="True if a Nokia Network as Code tool should be invoked")
    tool_name: str | None = Field(default=None, description="Name of the permitted tool to invoke")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool call")
    rationale: str | None = Field(default=None, description="Rationale for querying this carrier capability")


class ContextEvaluator:
    """AI agent responsible for assessing risk and synthesizing canonical evidence into context.

    Can optionally query Nokia Network as Code tools via MCP to fetch supplemental carrier telemetry.
    All tool invocations are filtered and gated by a static allowlist.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        mcp_client: NokiaMCPClientProtocol | None = None,
    ) -> None:
        self.llm = llm_client
        self.mcp_client = mcp_client

    async def _query_mcp_tools(
        self,
        transaction: TransactionRequest,
        evidence: CanonicalEvidence,
        allowed_tools: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Advisory tool invocation step: allows the LLM to call a permitted Nokia MCP tool."""
        tools_summary = [
            {"name": t["name"], "description": t.get("description", "")}
            for t in allowed_tools
        ]

        prompt = f"""
Review the transaction and current canonical carrier evidence.
You have access to the following Nokia Network as Code MCP tools:
{json.dumps(tools_summary, indent=2)}

Transaction Details:
- Action: {transaction.action}
- Zone: {transaction.zone}
- Cargo category: {transaction.category}
- Declared value: ${transaction.value}
- Timestamp: {transaction.timestamp.isoformat()}

Canonical Evidence so far:
- Number Verified: {evidence.number_verified}
- Location Verified: {evidence.location_verified}
- Recent SIM Swap: {evidence.recent_sim_swap}

If you need to query an additional carrier network fact to assess risk, set call_tool=true, provide tool_name and arguments.
If the current evidence is already sufficient, set call_tool=false.
"""
        try:
            selection = await self.llm.generate_structured(
                prompt=prompt,
                response_model=ToolSelection,
                system_instruction="You are ZoneGate Security Context Evaluator. You decide whether an additional carrier MCP tool call is needed.",
            )

            if selection.call_tool and selection.tool_name and self.mcp_client:
                logger.info(
                    "Context Evaluator requested Nokia MCP tool '%s' with args %s",
                    selection.tool_name,
                    selection.arguments,
                )
                output = await self.mcp_client.call_tool(
                    selection.tool_name,
                    selection.arguments,
                )
                return {
                    "tool": selection.tool_name,
                    "arguments": selection.arguments,
                    "result": output,
                }
        except UnauthorizedMCPToolError as exc:
            logger.warning("Context Evaluator attempted unauthorized MCP tool call: %s", exc)
            return {"tool": selection.tool_name, "error": str(exc), "status": "REJECTED_BY_ALLOWLIST"}
        except Exception as exc:
            logger.warning("Optional Nokia MCP tool invocation failed safely: %s", exc)
            return {"error": str(exc), "status": "FAILED"}

        return None

    async def evaluate(
        self,
        transaction: TransactionRequest,
        evidence: CanonicalEvidence,
        enterprise_context: dict[str, str] | None = None,
    ) -> ContextEvaluation:
        evidence_dict = {
            "number_verified": evidence.number_verified,
            "location_verified": evidence.location_verified,
            "recent_sim_swap": evidence.recent_sim_swap,
            "recent_device_swap": evidence.recent_device_swap,
            "reachable": evidence.reachable,
            "kyc_match": evidence.kyc_match,
        }

        # Step 1: Optional MCP tool exploration if MCP client is configured and tools are available
        mcp_findings = None
        if self.mcp_client:
            try:
                allowed_tools = await self.mcp_client.list_tools()
                if allowed_tools:
                    mcp_findings = await self._query_mcp_tools(transaction, evidence, allowed_tools)
            except Exception as exc:
                logger.warning("Failed to list/invoke Nokia MCP tools safely: %s", exc)

        # Step 2: Final Context Evaluation synthesizing all evidence
        prompt = f"""
Evaluate the operational and carrier risk for this transaction based on the collected canonical network evidence.
Synthesize the facts and recommend an advisory control (APPROVE, HOLD, or DENY) with identified risk factors and clear rationale.

Transaction Details:
- Transaction ID: {transaction.transaction_id}
- Action: {transaction.action}
- Resource ID: {transaction.resource_id}
- Zone: {transaction.zone}
- Cargo category: {transaction.category}
- Declared value: ${transaction.value}
- Timestamp: {transaction.timestamp.isoformat()}
- Metadata: {transaction.metadata}

Carrier Network Evidence:
{evidence_dict}

Supplemental Nokia MCP Telemetry:
{mcp_findings or "None"}

Enterprise Context:
{enterprise_context or {}}

Provide structured output conforming to ContextEvaluation with risk_factors, recommended_control, and rationale.
"""
        try:
            evaluation = await self.llm.generate_structured(
                prompt=prompt,
                response_model=ContextEvaluation,
                system_instruction="You are ZoneGate AI Context Evaluator. You provide structured risk assessments based strictly on network evidence and transaction parameters.",
            )
            return evaluation
        except Exception as exc:
            logger.error("Context evaluation failed: %s", exc)
            raise ContextEvaluationError(f"AI context evaluation failed: {exc}") from exc
