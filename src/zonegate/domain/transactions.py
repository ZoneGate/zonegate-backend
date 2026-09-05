from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class TransactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(..., description="Unique transaction correlation identifier")
    actor_id: str = Field(..., description="Initiating human actor identifier")
    action: str = Field(..., description="Protected action (e.g. RELEASE_CARGO)")
    resource_id: str = Field(..., description="Target asset or resource identifier")
    zone: str = Field(..., description="Physical or logical geofence/zone identifier")
    timestamp: datetime = Field(..., description="Timestamp of transaction initiation")
    value: Decimal = Field(default=Decimal("0.0"), description="Monetary or operational asset valuation")
    metadata: dict[str, str] = Field(default_factory=dict, description="Structured contextual key-value metadata")
