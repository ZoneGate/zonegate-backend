from datetime import datetime
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class RecommendedControl(StrEnum):
    APPROVE = "APPROVE"
    HOLD = "HOLD"
    DENY = "DENY"


class DecisionOutcome(StrEnum):
    APPROVE = "APPROVE"
    HOLD = "HOLD"
    DENY = "DENY"


class ContextEvaluation(BaseModel):
    """Advisory context evaluation produced by the AI Context Evaluator."""
    model_config = ConfigDict(extra="forbid")

    risk_factors: list[str] = Field(
        default_factory=list,
        description="Identified risk factors based on canonical evidence and context",
    )
    recommended_control: RecommendedControl = Field(
        ...,
        description="Advisory recommendation. NEVER directly becomes the authorization decision.",
    )
    rationale: str = Field(
        ...,
        description="Structured explanation supporting the advisory recommendation",
    )


class PolicyDecision(BaseModel):
    """Authoritative authorization decision produced strictly by the deterministic Policy Engine."""
    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(..., description="Unique decision audit identifier")
    transaction_id: str = Field(..., description="Correlated transaction identifier")
    decision: DecisionOutcome = Field(..., description="Authoritative outcome: APPROVE | HOLD | DENY")
    reasons: list[str] = Field(..., description="Deterministic policy rule justifications")
    required_authority: str | None = Field(
        default=None,
        description="Human approval role required if decision is HOLD",
    )
    context_evaluation: ContextEvaluation | None = Field(
        default=None,
        description="Advisory AI context evaluation provided to the policy engine",
    )
    evidence_summary: dict[str, Any] | None = Field(
        default=None,
        description="Sanitized summary of canonical evidence used in policy evaluation",
    )
    decided_at: datetime = Field(..., description="UTC timestamp of the policy decision")
