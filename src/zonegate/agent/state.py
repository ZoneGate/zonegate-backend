from typing import TypedDict
from zonegate.domain.decisions import ContextEvaluation
from zonegate.domain.evidence import CanonicalEvidence, EvidenceKind, EvidencePlan
from zonegate.domain.transactions import TransactionRequest


class AgentState(TypedDict, total=False):
    transaction: TransactionRequest
    workflow_name: str
    allowed_optional: list[EvidenceKind]
    evidence_plan: EvidencePlan | None
    canonical_evidence: CanonicalEvidence | None
    context_evaluation: ContextEvaluation | None
    error: str | None
