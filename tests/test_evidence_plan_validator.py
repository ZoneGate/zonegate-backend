import pytest
from zonegate.domain.evidence import EvidenceKind, EvidencePlan
from zonegate.evidence.plan_validator import (
    EvidencePlanValidator,
    ForbiddenEvidenceError,
    UnsupportedEvidenceError,
)


def test_release_cargo_always_contains_mandatory_evidence():
    validator = EvidencePlanValidator()
    # Case 1: Proposed plan is None
    plan = validator.validate("RELEASE_CARGO", proposed_plan=None)
    assert EvidenceKind.NUMBER_VERIFICATION in plan.mandatory
    assert EvidenceKind.LOCATION_VERIFICATION in plan.mandatory
    assert EvidenceKind.NUMBER_VERIFICATION in plan.combined
    assert EvidenceKind.LOCATION_VERIFICATION in plan.combined
    assert plan.optional == []


def test_ai_cannot_remove_mandatory_evidence():
    validator = EvidencePlanValidator()
    # Case 2: AI returns empty optional evidence list
    empty_plan = EvidencePlan(optional_evidence=[], rationale=["No optional evidence required"])
    plan = validator.validate("RELEASE_CARGO", proposed_plan=empty_plan)

    # Mandatory baseline MUST still be collected
    assert EvidenceKind.NUMBER_VERIFICATION in plan.mandatory
    assert EvidenceKind.LOCATION_VERIFICATION in plan.mandatory
    assert set(plan.combined) == {
        EvidenceKind.NUMBER_VERIFICATION,
        EvidenceKind.LOCATION_VERIFICATION,
    }


def test_kyc_match_is_forbidden_for_cargo_handoff():
    validator = EvidencePlanValidator()
    # Case 3: AI attempts to request KYC_MATCH for RELEASE_CARGO
    forbidden_plan = EvidencePlan(
        optional_evidence=[EvidenceKind.KYC_MATCH],
        rationale=["Trying to request KYC match on cargo"],
    )

    with pytest.raises(ForbiddenEvidenceError) as exc_info:
        validator.validate("RELEASE_CARGO", proposed_plan=forbidden_plan)

    assert "strictly forbidden" in str(exc_info.value)
    assert "KYC_MATCH" in str(exc_info.value)


def test_valid_optional_evidence_is_accepted_and_combined():
    validator = EvidencePlanValidator()
    valid_plan = EvidencePlan(
        optional_evidence=[EvidenceKind.SIM_SWAP, EvidenceKind.DEVICE_SWAP],
        rationale=["High-value transaction at night requires SIM continuity"],
    )
    result = validator.validate("RELEASE_CARGO", proposed_plan=valid_plan)

    assert EvidenceKind.SIM_SWAP in result.optional
    assert EvidenceKind.DEVICE_SWAP in result.optional
    # Both mandatory baseline and valid optional are combined
    assert set(result.combined) == {
        EvidenceKind.NUMBER_VERIFICATION,
        EvidenceKind.LOCATION_VERIFICATION,
        EvidenceKind.SIM_SWAP,
        EvidenceKind.DEVICE_SWAP,
    }
