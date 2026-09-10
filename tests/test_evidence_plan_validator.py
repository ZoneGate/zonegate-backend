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


# --- The trace: who decided each part of the plan --------------------------
#
# The plan that reaches the gateway is a merge of two sources -- policy and an
# advisory agent -- and an auditor has to be able to tell them apart after the
# fact. These pin the record that makes that possible.


def test_the_plan_records_what_the_agent_was_offered():
    # The agent chooses from the workflow's optional set and nothing else, so
    # the offered list is the boundary its influence is confined to.
    plan = EvidencePlanValidator().validate(workflow_name="RELEASE_CARGO")

    assert set(plan.offered_optional) == {
        EvidenceKind.SIM_SWAP,
        EvidenceKind.DEVICE_SWAP,
        EvidenceKind.REACHABILITY,
    }
    assert EvidenceKind.NUMBER_VERIFICATION not in plan.offered_optional
    assert EvidenceKind.LOCATION_VERIFICATION not in plan.offered_optional


def test_the_plan_records_what_the_agent_actually_asked_for():
    proposed = EvidencePlan(
        optional_evidence=[EvidenceKind.SIM_SWAP],
        rationale=["high value outside the operating window"],
    )

    plan = EvidencePlanValidator().validate(
        workflow_name="RELEASE_CARGO", proposed_plan=proposed
    )

    assert plan.proposed_optional == [EvidenceKind.SIM_SWAP]
    assert plan.planner_consulted is True
    assert plan.rationale == ["high value outside the operating window"]


def test_mandatory_evidence_is_attributable_to_policy_not_to_the_agent():
    # The whole point of the record: number and location verification are in
    # the collected set even though the agent never asked for them, and could
    # not have.
    proposed = EvidencePlan(optional_evidence=[EvidenceKind.SIM_SWAP])

    plan = EvidencePlanValidator().validate(
        workflow_name="RELEASE_CARGO", proposed_plan=proposed
    )

    enforced = [kind for kind in plan.combined if kind not in plan.proposed_optional]

    assert set(enforced) == {
        EvidenceKind.NUMBER_VERIFICATION,
        EvidenceKind.LOCATION_VERIFICATION,
    }


def test_a_failed_planner_is_recorded_as_never_consulted():
    # A plan with no agent behind it must not read as "the agent decided that
    # nothing extra was needed" -- those are different facts.
    plan = EvidencePlanValidator().validate(workflow_name="RELEASE_CARGO")

    assert plan.planner_consulted is False
    assert plan.proposed_optional == []
    assert plan.optional == []
    assert set(plan.combined) == {
        EvidenceKind.NUMBER_VERIFICATION,
        EvidenceKind.LOCATION_VERIFICATION,
    }


def test_an_agent_asking_for_nothing_is_distinguishable_from_no_agent():
    plan = EvidencePlanValidator().validate(
        workflow_name="RELEASE_CARGO", proposed_plan=EvidencePlan()
    )

    assert plan.planner_consulted is True
    assert plan.proposed_optional == []


def test_a_forbidden_proposal_is_refused_rather_than_quietly_dropped():
    # Dropping it would leave a plan that looks compliant while hiding that the
    # agent asked for something it must never have.
    proposed = EvidencePlan(optional_evidence=[EvidenceKind.KYC_MATCH])

    with pytest.raises(ForbiddenEvidenceError):
        EvidencePlanValidator().validate(
            workflow_name="RELEASE_CARGO", proposed_plan=proposed
        )
