"""Exhaustive coverage of the deterministic policy engine.

The engine is the only thing that decides. Everything else in the system —
the agent, the console, the mobile app — feeds it or reports what it said, so
each rule, each boundary between rules, and the order they fire in is pinned
down here.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from zonegate.domain.actors import Actor
from zonegate.domain.decisions import ContextEvaluation, DecisionOutcome
from zonegate.domain.evidence import CanonicalEvidence
from zonegate.domain.policy_config import PolicyConfig
from zonegate.domain.transactions import TransactionRequest
from zonegate.policy.engine import PolicyEngine


def actor(permissions: list[str] | None = None) -> Actor:
    return Actor(
        actor_id="usr_cargo_operator_01",
        role="ROLE_CARGO_OPERATOR",
        permissions=["cargo:release"] if permissions is None else permissions,
        registered_phone_number="+14155550199",
        registered_device_id="dev_imei_99887766",
        enrollment_status="ACTIVE",
    )


def transaction(
    value: str = "15000.00",
    hour: int = 14,
    action: str = "RELEASE_CARGO",
    category: str = "GENERAL",
) -> TransactionRequest:
    return TransactionRequest(
        transaction_id="tx_rule_test",
        actor_id="usr_cargo_operator_01",
        action=action,
        resource_id="CT-100",
        zone="PORT_GATE_17",
        timestamp=datetime(2026, 9, 10, hour, 30, tzinfo=timezone.utc),
        value=Decimal(value),
        category=category,
    )


def evidence(
    number: bool | None = True,
    location: bool | None = True,
    sim_swap: bool | None = False,
    device_swap: bool | None = False,
    reachable: bool | None = True,
) -> CanonicalEvidence:
    return CanonicalEvidence(
        number_verified=number,
        location_verified=location,
        recent_sim_swap=sim_swap,
        recent_device_swap=device_swap,
        reachable=reachable,
        collected_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Rule 1 — permission
# ---------------------------------------------------------------------------


def test_missing_permission_denies_before_any_evidence_is_considered():
    """A permission failure short-circuits: perfect evidence must not rescue it."""
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(permissions=["cargo:inspect"]),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.DENY
    assert "cargo:release" in decision.reasons[0]


def test_permission_check_falls_back_to_a_derived_name_for_unmapped_actions():
    """An action with no mapping still requires a permission, never none."""
    decision = PolicyEngine().evaluate(
        transaction=transaction(action="SEAL_CONTAINER"),
        actor=actor(permissions=["cargo:release"]),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.DENY
    assert "seal_container:execute" in decision.reasons[0]


def test_unmapped_action_is_allowed_when_the_derived_permission_is_held():
    decision = PolicyEngine().evaluate(
        transaction=transaction(action="SEAL_CONTAINER"),
        actor=actor(permissions=["seal_container:execute"]),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.APPROVE


def test_an_actor_with_no_permissions_at_all_is_denied():
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(permissions=[]),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.DENY


# ---------------------------------------------------------------------------
# Rule 2 — number verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("number_state", [False, None])
def test_number_not_positively_verified_denies(number_state):
    """Unverified is treated as failed: absence of proof is not proof."""
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(number=number_state),
    )

    assert decision.decision == DecisionOutcome.DENY
    assert "number verification" in decision.reasons[0].lower()


def test_a_check_nobody_answered_is_not_reported_as_a_failed_check():
    """Both deny, but the operator needs to know which of the two happened.

    A failed check is a finding about this request. An uncollected one is a
    finding about the deployment -- the carrier was never asked, or would not
    answer -- and only the second is something the reader can go and fix.
    """
    not_collected = PolicyEngine().evaluate(
        transaction=transaction(), actor=actor(), evidence=evidence(number=None)
    )
    failed = PolicyEngine().evaluate(
        transaction=transaction(), actor=actor(), evidence=evidence(number=False)
    )

    assert not_collected.decision == DecisionOutcome.DENY
    assert failed.decision == DecisionOutcome.DENY
    assert "not collected" in not_collected.reasons[0].lower()
    assert "not collected" not in failed.reasons[0].lower()
    assert not_collected.reasons[0] != failed.reasons[0]


def test_number_failure_outranks_a_location_failure_in_the_reason_given():
    """Rule order decides which failure the operator is told about first."""
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(number=False, location=False),
    )

    assert decision.decision == DecisionOutcome.DENY
    assert "number verification" in decision.reasons[0].lower()


# ---------------------------------------------------------------------------
# Rule 3 — location geofence
# ---------------------------------------------------------------------------


def test_location_explicitly_false_denies():
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(location=False),
    )

    assert decision.decision == DecisionOutcome.DENY
    assert "geofence" in decision.reasons[0].lower()


def test_location_not_collected_does_not_deny_on_its_own():
    """`None` means the planner never asked, which is not a failed check."""
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(location=None),
    )

    assert decision.decision == DecisionOutcome.APPROVE


# ---------------------------------------------------------------------------
# Rule 4 — a recent SIM swap
# ---------------------------------------------------------------------------


def test_sim_swap_holds_for_the_security_officer():
    """A swapped SIM puts the line the evidence came over in doubt.

    Nothing about the cargo makes that safe, so the swap holds on its own
    rather than only in combination with what is being released.
    """
    decision = PolicyEngine().evaluate(
        transaction=transaction(category="GENERAL", hour=14),
        actor=actor(),
        evidence=evidence(sim_swap=True),
    )

    assert decision.decision == DecisionOutcome.HOLD
    assert decision.required_authority == "ROLE_SECURITY_OFFICER"


def test_sim_swap_not_collected_does_not_hold():
    """NOT COLLECTED is not the same as a clean line; it just cannot trip the rule."""
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(sim_swap=None),
    )

    assert decision.decision == DecisionOutcome.APPROVE


def test_sim_swap_outranks_the_category_hold():
    """Both hold; the earlier rule decides which authority is named."""
    decision = PolicyEngine().evaluate(
        transaction=transaction(category="WEAPONS", hour=14),
        actor=actor(),
        evidence=evidence(sim_swap=True),
    )

    assert decision.decision == DecisionOutcome.HOLD
    assert decision.required_authority == "ROLE_SECURITY_OFFICER"


# ---------------------------------------------------------------------------
# Rule 5 — restricted cargo categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category,authority",
    [
        ("HIGH_VALUE", "ROLE_CARGO_SUPERVISOR"),
        ("HAZARDOUS", "ROLE_SAFETY_OFFICER"),
        ("CONTROLLED_SUBSTANCE", "ROLE_COMPLIANCE_OFFICER"),
        ("WEAPONS", "ROLE_SECURITY_OFFICER"),
    ],
)
def test_each_restricted_category_names_its_own_authority(category, authority):
    decision = PolicyEngine().evaluate(
        transaction=transaction(category=category, hour=14),
        actor=actor(),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.HOLD
    assert decision.required_authority == authority
    assert category in decision.reasons[0]


@pytest.mark.parametrize("category", ["GENERAL", "PERISHABLE"])
def test_an_unrestricted_category_releases_on_clean_evidence(category):
    decision = PolicyEngine().evaluate(
        transaction=transaction(category=category, hour=14),
        actor=actor(),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.APPROVE


def test_declared_value_alone_no_longer_holds_anything():
    """Value is recorded for the audit trail; the category is what escalates."""
    decision = PolicyEngine().evaluate(
        transaction=transaction(value="5000000.00", category="GENERAL", hour=14),
        actor=actor(),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.APPROVE


def test_a_category_removed_from_the_config_stops_being_restricted():
    engine = PolicyEngine(
        config=PolicyConfig(restricted_categories={"WEAPONS": "ROLE_SECURITY_OFFICER"})
    )

    decision = engine.evaluate(
        transaction=transaction(category="HAZARDOUS", hour=14),
        actor=actor(),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.APPROVE


def test_a_category_added_to_the_config_starts_being_restricted():
    engine = PolicyEngine(
        config=PolicyConfig(restricted_categories={"PERISHABLE": "ROLE_COLD_CHAIN_LEAD"})
    )

    decision = engine.evaluate(
        transaction=transaction(category="PERISHABLE", hour=14),
        actor=actor(),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.HOLD
    assert decision.required_authority == "ROLE_COLD_CHAIN_LEAD"


def test_a_restricted_category_with_no_authority_is_rejected_as_configuration():
    """A category nobody has to approve would release while looking restricted."""
    with pytest.raises(ValueError):
        PolicyConfig(restricted_categories={"WEAPONS": "  "})


# ---------------------------------------------------------------------------
# Rule 6 — the operational window
# ---------------------------------------------------------------------------


def test_outside_the_window_holds_for_the_cargo_supervisor():
    decision = PolicyEngine().evaluate(
        transaction=transaction(category="GENERAL", hour=3),
        actor=actor(),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.HOLD
    assert decision.required_authority == "ROLE_CARGO_SUPERVISOR"


@pytest.mark.parametrize(
    "hour,outside",
    [
        (5, True),   # the hour before the window opens
        (6, False),  # the window opens on this hour
        (19, False), # still the last hour inside
        (20, True),  # the window closes on this hour
        (0, True),
        (23, True),
    ],
)
def test_window_boundaries_are_inclusive_of_start_and_exclusive_of_end(hour, outside):
    engine = PolicyEngine()
    stamp = datetime(2026, 9, 10, hour, 0, tzinfo=timezone.utc)

    assert engine.is_outside_expected_window(stamp) is outside


def test_a_retuned_window_changes_which_requests_are_held():
    engine = PolicyEngine(config=PolicyConfig(window_start_hour=8, window_end_hour=17))

    # 07:30 sits inside the default window but outside this one.
    held = engine.evaluate(
        transaction=transaction(hour=7),
        actor=actor(),
        evidence=evidence(),
    )
    assert held.decision == DecisionOutcome.HOLD

    approved = engine.evaluate(
        transaction=transaction(hour=9),
        actor=actor(),
        evidence=evidence(),
    )
    assert approved.decision == DecisionOutcome.APPROVE


def test_the_category_hold_outranks_the_window_hold():
    decision = PolicyEngine().evaluate(
        transaction=transaction(category="HAZARDOUS", hour=3),
        actor=actor(),
        evidence=evidence(),
    )

    assert decision.required_authority == "ROLE_SAFETY_OFFICER"


def test_a_deny_outranks_every_hold_rule():
    decision = PolicyEngine().evaluate(
        transaction=transaction(category="WEAPONS", hour=3),
        actor=actor(),
        evidence=evidence(location=False, sim_swap=True),
    )

    assert decision.decision == DecisionOutcome.DENY


# ---------------------------------------------------------------------------
# Rule 7 — approval, and what never influences it
# ---------------------------------------------------------------------------


def test_clean_evidence_inside_the_window_approves():
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(),
    )

    assert decision.decision == DecisionOutcome.APPROVE
    assert decision.required_authority is None


def test_device_swap_alone_never_changes_the_outcome():
    """No rule reads device swap; it is recorded as evidence, not acted on."""
    decision = PolicyEngine().evaluate(
        transaction=transaction(value="500000.00", hour=14),
        actor=actor(),
        evidence=evidence(device_swap=True),
    )

    assert decision.decision == DecisionOutcome.APPROVE
    assert decision.evidence_summary is not None
    assert decision.evidence_summary["recent_device_swap"] is True


def test_unreachable_device_alone_never_changes_the_outcome():
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(reachable=False),
    )

    assert decision.decision == DecisionOutcome.APPROVE


@pytest.mark.parametrize(
    "recommendation",
    [DecisionOutcome.APPROVE, DecisionOutcome.HOLD, DecisionOutcome.DENY],
)
def test_no_agent_recommendation_can_move_a_deterministic_outcome(recommendation):
    """The agent is advisory in every direction, not just the permissive one."""
    baseline = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(),
    )

    advised = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(),
        context_evaluation=ContextEvaluation(
            risk_factors=["ANOMALOUS_PATTERN"],
            recommended_control=recommendation,
            rationale="Advisory only",
        ),
    )

    assert advised.decision == baseline.decision == DecisionOutcome.APPROVE
    assert advised.context_evaluation is not None
    assert advised.context_evaluation.recommended_control == recommendation


# ---------------------------------------------------------------------------
# Record shape
# ---------------------------------------------------------------------------


def test_every_decision_carries_the_evidence_it_was_made_on():
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(sim_swap=None, reachable=False),
    )

    summary = decision.evidence_summary
    assert summary is not None
    assert summary["number_verified"] is True
    assert summary["recent_sim_swap"] is None
    assert summary["reachable"] is False


def test_decision_ids_are_unique_across_evaluations():
    engine = PolicyEngine()
    ids = {
        engine.evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(),
        ).decision_id
        for _ in range(25)
    }

    assert len(ids) == 25


def test_the_decision_is_correlated_to_the_transaction_it_judged():
    decision = PolicyEngine().evaluate(
        transaction=transaction(),
        actor=actor(),
        evidence=evidence(),
    )

    assert decision.transaction_id == "tx_rule_test"
    assert decision.resolution is None
