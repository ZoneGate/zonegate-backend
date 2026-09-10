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


class HoldResolution(BaseModel):
    """Final, binding decision made by the human authority a HOLD was handed to.

    Only a HOLD can be resolved. A deterministic DENY is never overridable.
    """
    model_config = ConfigDict(extra="forbid")

    outcome: DecisionOutcome = Field(..., description="Final human outcome: APPROVE | DENY")
    resolved_by: str = Field(..., description="Identifier of the human authority who decided")
    authority_role: str = Field(..., description="Operational role the policy engine required")
    note: str = Field(default="", description="Free-text justification recorded for audit")
    resolved_at: datetime = Field(..., description="UTC timestamp of the human decision")


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
    resolution: "HoldResolution | None" = Field(
        default=None,
        description="Human authority resolution, present only once a HOLD has been decided",
    )
