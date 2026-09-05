from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from zonegate.domain.decisions import DecisionOutcome


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str = Field(..., description="Unique receipt identifier")
    decision_id: str = Field(..., description="Underlying policy decision identifier")
    transaction_id: str = Field(..., description="Target transaction identifier")
    decision: DecisionOutcome = Field(..., description="Policy decision outcome")
    issued_at: datetime = Field(..., description="Timestamp of receipt generation")
    token: str | None = Field(
        default=None,
        description="Cryptographically scoped authorization token if decision is APPROVE",
    )
