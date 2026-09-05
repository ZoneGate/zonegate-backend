from datetime import datetime, timezone
from decimal import Decimal
import pytest
from zonegate.domain.actors import Actor
from zonegate.domain.decisions import (
    ContextEvaluation,
    DecisionOutcome,
    RecommendedControl,
)
from zonegate.domain.evidence import CanonicalEvidence
from zonegate.domain.transactions import TransactionRequest
from zonegate.policy.engine import PolicyEngine


def test_ai_recommend_approve_cannot_override_deterministic_deny(
    standard_actor: Actor,
    standard_transaction: TransactionRequest,
):
    """Architectural invariant: AI advisory APPROVE cannot override deterministic hard DENY."""
    engine = PolicyEngine()

    # Location verification failed (network says outside zone)
    failed_evidence = CanonicalEvidence(
        number_verified=True,
        location_verified=False,
        recent_sim_swap=False,
        recent_device_swap=False,
        reachable=True,
        collected_at=datetime.now(timezone.utc),
    )

    # Advisory AI erroneously or maliciously recommends APPROVE
    ai_advisory = ContextEvaluation(
        risk_factors=[],
        recommended_control=RecommendedControl.APPROVE,
        rationale="AI hallucinates that everything looks fine",
    )

    decision = engine.evaluate(
        transaction=standard_transaction,
        actor=standard_actor,
        evidence=failed_evidence,
        context_evaluation=ai_advisory,
    )

    assert decision.decision == DecisionOutcome.DENY
    assert any("geofence check failed" in r for r in decision.reasons)


def test_location_verified_false_causes_deny(
    standard_actor: Actor,
    standard_transaction: TransactionRequest,
):
    engine = PolicyEngine()
    evidence = CanonicalEvidence(
        number_verified=True,
        location_verified=False,
        collected_at=datetime.now(timezone.utc),
    )

    decision = engine.evaluate(
        transaction=standard_transaction,
        actor=standard_actor,
        evidence=evidence,
    )

    assert decision.decision == DecisionOutcome.DENY
    assert any("outside authorized zone" in r for r in decision.reasons)


def test_number_verification_failed_causes_deny(
    standard_actor: Actor,
    standard_transaction: TransactionRequest,
):
    engine = PolicyEngine()
    evidence = CanonicalEvidence(
        number_verified=False,
        location_verified=True,
        collected_at=datetime.now(timezone.utc),
    )

    decision = engine.evaluate(
        transaction=standard_transaction,
        actor=standard_actor,
        evidence=evidence,
    )

    assert decision.decision == DecisionOutcome.DENY
    assert any("Subscriber number verification failed" in r for r in decision.reasons)


def test_high_value_and_outside_expected_window_causes_hold(
    standard_actor: Actor,
):
    engine = PolicyEngine(high_value_threshold=Decimal("100000.00"))

    # $2.8M at 02:15 AM
    night_tx = TransactionRequest(
        transaction_id="tx_night_high_val",
        actor_id=standard_actor.actor_id,
        action="RELEASE_CARGO",
        resource_id="cargo_valuable_999",
        zone="ZONE_CARGO_BAY_1",
        timestamp=datetime(2026, 9, 4, 2, 15, tzinfo=timezone.utc),
        value=Decimal("2800000.00"),
    )

    evidence = CanonicalEvidence(
        number_verified=True,
        location_verified=True,
        recent_sim_swap=False,
        collected_at=datetime.now(timezone.utc),
    )

    decision = engine.evaluate(
        transaction=night_tx,
        actor=standard_actor,
        evidence=evidence,
    )

    assert decision.decision == DecisionOutcome.HOLD
    assert decision.required_authority == "ROLE_CARGO_SUPERVISOR"
    assert any("outside expected operational window" in r for r in decision.reasons)


def test_recent_sim_swap_and_high_value_causes_hold(
    standard_actor: Actor,
):
    engine = PolicyEngine(high_value_threshold=Decimal("50000.00"))

    tx = TransactionRequest(
        transaction_id="tx_sim_swap_high_val",
        actor_id=standard_actor.actor_id,
        action="RELEASE_CARGO",
        resource_id="cargo_99",
        zone="ZONE_CARGO_BAY_1",
        timestamp=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),  # noon
        value=Decimal("150000.00"),
    )

    evidence = CanonicalEvidence(
        number_verified=True,
        location_verified=True,
        recent_sim_swap=True,
        collected_at=datetime.now(timezone.utc),
    )

    decision = engine.evaluate(
        transaction=tx,
        actor=standard_actor,
        evidence=evidence,
    )

    assert decision.decision == DecisionOutcome.HOLD
    assert decision.required_authority == "ROLE_SECURITY_OFFICER"
    assert any("Recent carrier SIM swap detected" in r for r in decision.reasons)


def test_standard_valid_transaction_approves(
    standard_actor: Actor,
    standard_transaction: TransactionRequest,
):
    engine = PolicyEngine()
    evidence = CanonicalEvidence(
        number_verified=True,
        location_verified=True,
        recent_sim_swap=False,
        collected_at=datetime.now(timezone.utc),
    )

    decision = engine.evaluate(
        transaction=standard_transaction,
        actor=standard_actor,
        evidence=evidence,
    )

    assert decision.decision == DecisionOutcome.APPROVE
    assert decision.required_authority is None
