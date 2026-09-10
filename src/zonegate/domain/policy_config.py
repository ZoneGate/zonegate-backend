from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class PolicyConfig(BaseModel):
    """The thresholds the deterministic engine evaluates against.

    These are the knobs an operations lead is allowed to turn. Everything else
    about a decision — which rules exist, their order, and the fact that the
    agent's assessment is advisory — is fixed in code and deliberately not
    configurable from an API.
    """

    model_config = ConfigDict(extra="forbid")

    high_value_threshold: Decimal = Field(
        default=Decimal("100000.00"),
        gt=0,
        description="At or above this value a transaction counts as high-value",
    )
    window_start_hour: int = Field(
        default=6,
        ge=0,
        le=23,
        description="First hour (UTC) of the expected operational window",
    )
    window_end_hour: int = Field(
        default=20,
        ge=1,
        le=24,
        description="Hour (UTC) the expected operational window closes",
    )

    def model_post_init(self, _context: object) -> None:
        if self.window_end_hour <= self.window_start_hour:
            raise ValueError(
                "window_end_hour must be later than window_start_hour"
            )
