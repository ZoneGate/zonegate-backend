from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class EvidenceKind(StrEnum):
    NUMBER_VERIFICATION = "NUMBER_VERIFICATION"
    LOCATION_VERIFICATION = "LOCATION_VERIFICATION"
    SIM_SWAP = "SIM_SWAP"
    DEVICE_SWAP = "DEVICE_SWAP"
    REACHABILITY = "REACHABILITY"
    KYC_MATCH = "KYC_MATCH"


class EvidencePlan(BaseModel):
    """Advisory evidence plan proposed by AI Evidence Planner.

    The AI is only permitted to propose optional evidence. It cannot redefine or suppress
    mandatory baseline evidence.
    """
    model_config = ConfigDict(extra="forbid")

    optional_evidence: list[EvidenceKind] = Field(
        default_factory=list,
        description="Optional evidence kinds selected by the AI planner",
    )
    rationale: list[str] = Field(
        default_factory=list,
        description="Step-by-step rationale for why each optional evidence kind was selected",
    )


class ValidatedEvidencePlan(BaseModel):
    """Enforced, validated evidence plan combining mandatory and approved optional evidence."""
    model_config = ConfigDict(extra="forbid")

    mandatory: list[EvidenceKind] = Field(..., description="Mandatory baseline evidence for workflow")
    optional: list[EvidenceKind] = Field(default_factory=list, description="Validated optional evidence")
    combined: list[EvidenceKind] = Field(..., description="Complete set of evidence to be collected")
    rationale: list[str] = Field(default_factory=list, description="Planner rationale")


class CanonicalEvidence(BaseModel):
    """Normalized, network-attested evidence.

    Leaking raw third-party gateway (Nokia/CAMARA) payload representations into domain
    or policy engines is strictly forbidden.
    """
    model_config = ConfigDict(extra="forbid")

    number_verified: bool | None = Field(
        default=None,
        description="True if carrier confirms subscriber phone number matches device line",
    )
    location_verified: bool | None = Field(
        default=None,
        description="True if network cell/timing-advance geofence check confirms device in zone",
    )
    recent_sim_swap: bool | None = Field(
        default=None,
        description="True if carrier reports a SIM swap within the monitored window",
    )
    recent_device_swap: bool | None = Field(
        default=None,
        description="True if carrier reports an IMEI/hardware change within monitored window",
    )
    reachable: bool | None = Field(
        default=None,
        description="True if device is currently registered and attached to the carrier network",
    )
    kyc_match: bool | None = Field(
        default=None,
        description="True if carrier subscriber identity matches enterprise KYC records",
    )
    collected_at: datetime = Field(..., description="UTC timestamp when network evidence was collected")
