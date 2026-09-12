"""Coverage of the embedded store.

Zova is the system of record for every decision ZoneGate has ever made, so
what it keeps, in what order, and what it does on a rewrite is the difference
between an audit trail and a pile of rows.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.decisions import (
    ContextEvaluation,
    DecisionOutcome,
    HoldResolution,
    PolicyDecision,
)
from zonegate.domain.evidence import CanonicalEvidence, EvidenceKind, ValidatedEvidencePlan
from zonegate.domain.policy_config import PolicyConfig
from zonegate.domain.receipts import Receipt
from zonegate.domain.transactions import TransactionRequest


def decision(
    decision_id: str,
    outcome: DecisionOutcome = DecisionOutcome.APPROVE,
    transaction_id: str | None = None,
    resolution: HoldResolution | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        decision_id=decision_id,
        transaction_id=transaction_id or f"tx_{decision_id}",
        decision=outcome,
        reasons=["stored for test"],
        required_authority="ROLE_CARGO_SUPERVISOR"
        if outcome == DecisionOutcome.HOLD
        else None,
        context_evaluation=None,
        evidence_summary=None,
        decided_at=datetime.now(timezone.utc),
        resolution=resolution,
    )


# ---------------------------------------------------------------------------
# Actors and bindings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_actor_round_trips_with_every_field_intact(memory_store):
    actor = Actor(
        actor_id="usr_round",
        role="ROLE_SECURITY_OFFICER",
        permissions=["cargo:inspect", "security:override"],
        registered_phone_number="+14155550122",
        registered_device_id="dev_imei_44332211",
        enrollment_status="ACTIVE",
    )

    await memory_store.save_actor(actor)
    reread = await memory_store.get_actor("usr_round")

    assert reread == actor


@pytest.mark.asyncio
async def test_an_unknown_actor_reads_back_as_none(memory_store):
    assert await memory_store.get_actor("usr_nobody") is None


@pytest.mark.asyncio
async def test_the_roster_index_orders_by_most_recent_enrolment(memory_store):
    for actor_id in ("usr_1", "usr_2", "usr_3"):
        await memory_store.save_actor(
            Actor(
                actor_id=actor_id,
                role="ROLE_CARGO_OPERATOR",
                permissions=[],
                registered_phone_number="+1",
                registered_device_id="d",
                enrollment_status="ACTIVE",
            )
        )

    listed = await memory_store.list_actors()

    assert [a.actor_id for a in listed] == ["usr_3", "usr_2", "usr_1"]


@pytest.mark.asyncio
async def test_re_saving_an_actor_moves_them_to_the_front_without_duplicating(
    memory_store,
):
    for actor_id in ("usr_1", "usr_2"):
        await memory_store.save_actor(
            Actor(
                actor_id=actor_id,
                role="ROLE_CARGO_OPERATOR",
                permissions=[],
                registered_phone_number="+1",
                registered_device_id="d",
                enrollment_status="ACTIVE",
            )
        )

    await memory_store.save_actor(
        Actor(
            actor_id="usr_1",
            role="ROLE_CARGO_SUPERVISOR",
            permissions=["cargo:authorize"],
            registered_phone_number="+1",
            registered_device_id="d",
            enrollment_status="ACTIVE",
        )
    )

    listed = await memory_store.list_actors()

    assert [a.actor_id for a in listed] == ["usr_1", "usr_2"]
    assert listed[0].role == "ROLE_CARGO_SUPERVISOR"


@pytest.mark.asyncio
async def test_the_roster_limit_truncates_from_the_newest_end(memory_store):
    for index in range(5):
        await memory_store.save_actor(
            Actor(
                actor_id=f"usr_{index}",
                role="ROLE_CARGO_OPERATOR",
                permissions=[],
                registered_phone_number="+1",
                registered_device_id="d",
                enrollment_status="ACTIVE",
            )
        )

    listed = await memory_store.list_actors(limit=2)

    assert [a.actor_id for a in listed] == ["usr_4", "usr_3"]


@pytest.mark.asyncio
async def test_a_device_binding_round_trips(memory_store):
    binding = DeviceBinding(
        actor_id="usr_bind",
        phone_number="+14155550199",
        device_id="dev_imei_99887766",
        bound_at=datetime.now(timezone.utc),
        is_active=True,
    )

    await memory_store.save_device_binding(binding)
    reread = await memory_store.get_device_binding("usr_bind")

    assert reread is not None
    assert reread.device_id == "dev_imei_99887766"
    assert reread.is_active is True


@pytest.mark.asyncio
async def test_rebinding_replaces_the_previous_device(memory_store):
    """An actor has one binding; a stale second one would be a second key."""
    for device in ("dev_old", "dev_new"):
        await memory_store.save_device_binding(
            DeviceBinding(
                actor_id="usr_rebind",
                phone_number="+14155550199",
                device_id=device,
                bound_at=datetime.now(timezone.utc),
                is_active=True,
            )
        )

    reread = await memory_store.get_device_binding("usr_rebind")

    assert reread is not None
    assert reread.device_id == "dev_new"


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_decision_round_trips_with_its_resolution(memory_store):
    resolved = decision(
        "dec_res",
        DecisionOutcome.HOLD,
        resolution=HoldResolution(
            outcome=DecisionOutcome.APPROVE,
            resolved_by="usr_cargo_supervisor_02",
            authority_role="ROLE_CARGO_SUPERVISOR",
            note="cleared",
            resolved_at=datetime.now(timezone.utc),
        ),
    )

    await memory_store.save_decision(resolved)
    reread = await memory_store.get_decision("dec_res")

    assert reread is not None
    assert reread.decision == DecisionOutcome.HOLD
    assert reread.resolution is not None
    assert reread.resolution.resolved_by == "usr_cargo_supervisor_02"


@pytest.mark.asyncio
async def test_decisions_list_newest_first(memory_store):
    for index in range(4):
        await memory_store.save_decision(decision(f"dec_{index}"))

    listed = await memory_store.list_decisions(limit=10)

    assert [d.decision_id for d in listed] == ["dec_3", "dec_2", "dec_1", "dec_0"]


@pytest.mark.asyncio
async def test_updating_a_decision_moves_it_to_the_front_without_duplicating(
    memory_store,
):
    """Resolving a HOLD re-saves it; the queue must not then show it twice."""
    await memory_store.save_decision(decision("dec_a", DecisionOutcome.HOLD))
    await memory_store.save_decision(decision("dec_b"))

    await memory_store.save_decision(
        decision(
            "dec_a",
            DecisionOutcome.HOLD,
            resolution=HoldResolution(
                outcome=DecisionOutcome.DENY,
                resolved_by="usr_security_officer_07",
                authority_role="ROLE_SECURITY_OFFICER",
                note="",
                resolved_at=datetime.now(timezone.utc),
            ),
        )
    )

    listed = await memory_store.list_decisions(limit=10)

    assert [d.decision_id for d in listed] == ["dec_a", "dec_b"]
    assert listed[0].resolution is not None


@pytest.mark.asyncio
async def test_listing_filters_by_outcome(memory_store):
    await memory_store.save_decision(decision("dec_ok", DecisionOutcome.APPROVE))
    await memory_store.save_decision(decision("dec_hold", DecisionOutcome.HOLD))
    await memory_store.save_decision(decision("dec_deny", DecisionOutcome.DENY))

    holds = await memory_store.list_decisions(outcome="HOLD", limit=10)

    assert [d.decision_id for d in holds] == ["dec_hold"]


@pytest.mark.asyncio
async def test_a_decision_can_be_found_by_the_transaction_it_judged(memory_store):
    await memory_store.save_decision(decision("dec_tx", transaction_id="tx_lookup"))

    found = await memory_store.get_decision_by_transaction_id("tx_lookup")

    assert found is not None
    assert found.decision_id == "dec_tx"


@pytest.mark.asyncio
async def test_an_unknown_transaction_has_no_decision(memory_store):
    assert await memory_store.get_decision_by_transaction_id("tx_none") is None


# ---------------------------------------------------------------------------
# Transactions, evidence and plans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_transaction_round_trips_including_its_metadata(memory_store):
    transaction = TransactionRequest(
        transaction_id="tx_meta",
        actor_id="usr_cargo_operator_01",
        action="RELEASE_CARGO",
        resource_id="CT-900",
        zone="PORT_GATE_17",
        timestamp=datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc),
        value=Decimal("12345.67"),
        metadata={"carrier": "Northline", "lane": "04"},
    )

    await memory_store.save_transaction(transaction)
    reread = await memory_store.get_transaction("tx_meta")

    assert reread is not None
    assert reread.value == Decimal("12345.67")
    assert reread.metadata == {"carrier": "Northline", "lane": "04"}


@pytest.mark.asyncio
async def test_evidence_round_trips_including_uncollected_fields(memory_store):
    """`None` has to survive the round trip; it is not the same as False."""
    evidence = CanonicalEvidence(
        number_verified=True,
        location_verified=None,
        recent_sim_swap=None,
        recent_device_swap=False,
        reachable=True,
        collected_at=datetime.now(timezone.utc),
    )

    await memory_store.save_evidence("tx_ev", evidence)
    reread = await memory_store.get_evidence("tx_ev")

    assert reread is not None
    assert reread.location_verified is None
    assert reread.recent_sim_swap is None
    assert reread.recent_device_swap is False


@pytest.mark.asyncio
async def test_an_evidence_plan_round_trips(memory_store):
    plan = ValidatedEvidencePlan(
        mandatory=[EvidenceKind.NUMBER_VERIFICATION],
        optional=[EvidenceKind.LOCATION_VERIFICATION],
        combined=[
            EvidenceKind.NUMBER_VERIFICATION,
            EvidenceKind.LOCATION_VERIFICATION,
        ],
        rationale=["baseline", "zone is high risk"],
    )

    await memory_store.save_evidence_plan("tx_plan", plan)
    reread = await memory_store.get_evidence_plan("tx_plan")

    assert reread is not None
    assert EvidenceKind.LOCATION_VERIFICATION in reread.combined
    assert reread.rationale == ["baseline", "zone is high risk"]


@pytest.mark.asyncio
async def test_a_context_evaluation_round_trips(memory_store):
    evaluation = ContextEvaluation(
        risk_factors=["OFF_HOURS", "HIGH_VALUE"],
        recommended_control=DecisionOutcome.HOLD,
        rationale="Unusual for this actor",
    )

    await memory_store.save_context_evaluation("tx_eval", evaluation)
    reread = await memory_store.get_context_evaluation("tx_eval")

    assert reread is not None
    assert reread.risk_factors == ["OFF_HOURS", "HIGH_VALUE"]
    assert reread.recommended_control == DecisionOutcome.HOLD


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_receipt_is_reachable_by_id_and_by_transaction(memory_store):
    receipt = Receipt(
        receipt_id="rcpt_1",
        decision_id="dec_1",
        transaction_id="tx_1",
        decision=DecisionOutcome.APPROVE,
        issued_at=datetime.now(timezone.utc),
        token="signature",
    )

    await memory_store.save_receipt(receipt)

    assert (await memory_store.get_receipt("rcpt_1")) is not None
    by_tx = await memory_store.get_receipt_by_transaction_id("tx_1")
    assert by_tx is not None
    assert by_tx.receipt_id == "rcpt_1"


@pytest.mark.asyncio
async def test_re_saving_a_receipt_updates_it_in_place(memory_store):
    """Resolving a HOLD re-issues the receipt against the same transaction."""
    issued = datetime.now(timezone.utc)

    await memory_store.save_receipt(
        Receipt(
            receipt_id="rcpt_2",
            decision_id="dec_2",
            transaction_id="tx_2",
            decision=DecisionOutcome.HOLD,
            issued_at=issued,
            token=None,
        )
    )

    await memory_store.save_receipt(
        Receipt(
            receipt_id="rcpt_2",
            decision_id="dec_2",
            transaction_id="tx_2",
            decision=DecisionOutcome.HOLD,
            issued_at=issued + timedelta(minutes=5),
            token="now-signed",
        )
    )

    reread = await memory_store.get_receipt_by_transaction_id("tx_2")

    assert reread is not None
    assert reread.token == "now-signed"


# ---------------------------------------------------------------------------
# Policy configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_config_is_absent_until_it_is_saved(memory_store):
    """Absent means 'never configured', which is why the engine falls back."""
    assert await memory_store.get_policy_config() is None


@pytest.mark.asyncio
async def test_policy_config_round_trips_with_its_category_map(memory_store):
    await memory_store.save_policy_config(
        PolicyConfig(
            restricted_categories={
                "HAZARDOUS": "ROLE_SAFETY_OFFICER",
                "WEAPONS": "ROLE_SECURITY_OFFICER",
            },
            window_start_hour=7,
            window_end_hour=19,
        )
    )

    reread = await memory_store.get_policy_config()

    assert reread is not None
    assert reread.authority_for("HAZARDOUS") == "ROLE_SAFETY_OFFICER"
    assert reread.authority_for("GENERAL") is None
    assert reread.window_start_hour == 7


@pytest.mark.asyncio
async def test_saving_policy_config_twice_keeps_only_the_latest(memory_store):
    for authority in ("ROLE_CARGO_SUPERVISOR", "ROLE_SECURITY_OFFICER"):
        await memory_store.save_policy_config(
            PolicyConfig(
                restricted_categories={"HIGH_VALUE": authority},
                window_start_hour=6,
                window_end_hour=20,
            )
        )

    reread = await memory_store.get_policy_config()

    assert reread is not None
    assert reread.restricted_categories == {"HIGH_VALUE": "ROLE_SECURITY_OFFICER"}


@pytest.mark.asyncio
async def test_a_policy_config_from_before_the_category_map_is_ignored_not_fatal(memory_store):
    """An existing deployment must not be unable to start after the upgrade."""
    memory_store._run_sync(
        lambda: memory_store._db.kv_put(
            memory_store.NS_INDEXES,
            memory_store.KEY_POLICY_CONFIG,
            b'{"high_value_threshold":"100000.00","window_start_hour":6,"window_end_hour":20}',
        )
    )

    assert await memory_store.get_policy_config() is None
