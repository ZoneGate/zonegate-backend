import logging
from zonegate.agent.base import LLMClientProtocol
from zonegate.domain.evidence import EvidenceKind, EvidencePlan
from zonegate.domain.transactions import TransactionRequest

logger = logging.getLogger(__name__)


class EvidencePlanningError(Exception):
    """Raised when evidence planning fails."""


class EvidencePlanner:
    """AI agent responsible for proposing optional network evidence.

    Advisory only. Mandatory evidence is enforced separately and cannot be suppressed.
    """

    def __init__(self, llm_client: LLMClientProtocol) -> None:
        self.llm = llm_client

    async def plan(
        self,
        transaction: TransactionRequest,
        workflow_name: str,
        allowed_optional: list[EvidenceKind],
    ) -> EvidencePlan:
        allowed_str = ", ".join(k.value for k in allowed_optional)
        prompt = f"""
Evaluate the transaction and determine if any OPTIONAL network evidence should be requested.
Mandatory evidence is already guaranteed and handled by the system.
You may ONLY choose from these allowed optional evidence kinds: [{allowed_str}].
If no additional evidence is necessary (e.g. standard value during business hours), return an empty optional_evidence list.
If high value, unusual hours, or high risk, select appropriate additional evidence such as SIM_SWAP or DEVICE_SWAP.

Transaction Context:
- Transaction ID: {transaction.transaction_id}
- Action: {transaction.action}
- Workflow: {workflow_name}
- Resource ID: {transaction.resource_id}
- Zone: {transaction.zone}
- Value: ${transaction.value}
- Timestamp: {transaction.timestamp.isoformat()}
- Metadata: {transaction.metadata}

Provide structured output conforming to EvidencePlan with optional_evidence and rationale.
"""
        try:
            plan = await self.llm.generate_structured(
                prompt=prompt,
                response_model=EvidencePlan,
                system_instruction="You are ZoneGate AI Evidence Planner. You select only necessary optional carrier evidence from the allowed set. Never invent evidence kinds.",
            )
            return plan
        except Exception as exc:
            logger.error("Evidence planning failed: %s", exc)
            raise EvidencePlanningError(f"AI evidence planning failed: {exc}") from exc
