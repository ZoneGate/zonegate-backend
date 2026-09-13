"""Coverage of the authority transfer a HOLD represents.

A HOLD is the only outcome that hands the decision to a person. What that
person may and may not do is the security boundary this file pins down: they
can clear or block a HOLD, they can never overturn a deterministic DENY, and
they can never decide the same HOLD twice.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from zonegate.authorization.service import (
    AuthorizationService,
    DecisionNotFoundError,
    HoldResolutionError,
)
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.decisions import DecisionOutcome
from zonegate.domain.transactions import TransactionRequest
from zonegate.evidence.gateway import EvidenceGateway
from zonegate.evidence.plan_validator import EvidencePlanValidator
from zonegate.agent.evidence_planner import EvidencePlanner
from zonegate.agent.context_evaluator import ContextEvaluator
from zonegate.authorization.token import TokenService
from zonegate.policy.engine import PolicyEngine



class UnavailableLLM:
    """Stands in for an agent runtime that is not reachable.

    Evidence planning then falls back to the mandatory baseline, which is the
    configuration the demo stack actually runs in.
    """

    async def generate_structured(self, *args, **kwargs):
        raise RuntimeError("agent runtime unavailable")

    async def health_check(self) -> bool:
        return False


def build_service(store, nokia) -> AuthorizationService:
    llm = UnavailableLLM()

    return AuthorizationService(
        store=store,
        gateway=EvidenceGateway(nokia_client=nokia),
        plan_validator=EvidencePlanValidator(),
        policy_engine=PolicyEngine(),
        token_service=TokenService(),
        evidence_planner=EvidencePlanner(llm_client=llm),
        context_evaluator=ContextEvaluator(llm_client=llm),
    )


async def seed_actor(store) -> Actor:
    actor = Actor(
        actor_id="usr_cargo_operator_01",
        role="ROLE_CARGO_OPERATOR",
        permissions=["cargo:release"],
        registered_phone_number="+14155550199",
        registered_device_id="dev_imei_99887766",
        enrollment_status="ACTIVE",
    )
    await store.save_actor(actor)
    await store.save_device_binding(
        DeviceBinding(
            actor_id=actor.actor_id,
            phone_number=actor.registered_phone_number,
            device_id=actor.registered_device_id,
            bound_at=datetime.now(timezone.utc),
            is_active=True,
        )
    )
    return actor


def tx(transaction_id: str, value: str, hour: int, category: str = "GENERAL") -> TransactionRequest:
    return TransactionRequest(
        transaction_id=transaction_id,
        actor_id="usr_cargo_operator_01",
        action="RELEASE_CARGO",
        resource_id="CT-500",
        zone="PORT_GATE_17",
        timestamp=datetime(2026, 9, 10, hour, 30, tzinfo=timezone.utc),
        value=Decimal(value),
        category=category,
    )


@pytest.mark.asyncio
async def test_resolving_a_hold_records_the_verdict_without_rewriting_the_engine(
    memory_store, fake_nokia_client
):
    """The stored decision stays HOLD; the human verdict is attached to it.

    Rewriting it to APPROVE would erase the fact that the engine held it, and
    with it the reason the transfer of authority ever happened.
    """
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    held, _ = await service.authorize_transaction(tx("tx_hold_1", "250000.00", 3, "HIGH_VALUE"))
    assert held.decision == DecisionOutcome.HOLD

    resolved, receipt = await service.resolve_hold(
        decision_id=held.decision_id,
        outcome=DecisionOutcome.APPROVE,
        resolved_by="usr_cargo_supervisor_02",
        note="Verified against the night manifest",
    )

    assert resolved.decision == DecisionOutcome.HOLD
    assert resolved.resolution is not None
    assert resolved.resolution.outcome == DecisionOutcome.APPROVE
    assert resolved.resolution.resolved_by == "usr_cargo_supervisor_02"
    assert resolved.resolution.note == "Verified against the night manifest"
    assert receipt.token is not None


@pytest.mark.asyncio
async def test_the_resolution_records_the_authority_the_engine_demanded(
    memory_store, fake_nokia_client
):
    """Who was *required* to decide is part of the audit trail, not just who did."""
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    held, _ = await service.authorize_transaction(tx("tx_hold_2", "250000.00", 3, "HIGH_VALUE"))

    resolved, _ = await service.resolve_hold(
        decision_id=held.decision_id,
        outcome=DecisionOutcome.APPROVE,
        resolved_by="usr_cargo_supervisor_02",
    )

    assert resolved.resolution is not None
    assert resolved.resolution.authority_role == "ROLE_CARGO_SUPERVISOR"


@pytest.mark.asyncio
async def test_a_human_deny_issues_no_token(memory_store, fake_nokia_client):
    """Blocking must not hand out the credential that would let the act proceed."""
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    held, _ = await service.authorize_transaction(tx("tx_hold_3", "250000.00", 3, "HIGH_VALUE"))

    resolved, receipt = await service.resolve_hold(
        decision_id=held.decision_id,
        outcome=DecisionOutcome.DENY,
        resolved_by="usr_security_officer_07",
        note="Manifest could not be corroborated",
    )

    assert resolved.resolution is not None
    assert resolved.resolution.outcome == DecisionOutcome.DENY
    assert receipt.token is None


@pytest.mark.asyncio
async def test_a_deterministic_deny_cannot_be_overturned_by_a_person(
    memory_store, fake_nokia_client
):
    """This is the core boundary: no human can clear what policy blocked."""
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    fake_nokia_client.location_verified = False
    denied, _ = await service.authorize_transaction(tx("tx_deny_1", "5000.00", 14))
    assert denied.decision == DecisionOutcome.DENY

    with pytest.raises(HoldResolutionError) as raised:
        await service.resolve_hold(
            decision_id=denied.decision_id,
            outcome=DecisionOutcome.APPROVE,
            resolved_by="usr_cargo_supervisor_02",
        )

    assert "not open to human resolution" in str(raised.value)


@pytest.mark.asyncio
async def test_an_approved_decision_is_not_open_to_resolution(
    memory_store, fake_nokia_client
):
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    approved, _ = await service.authorize_transaction(tx("tx_ok_1", "5000.00", 14))
    assert approved.decision == DecisionOutcome.APPROVE

    with pytest.raises(HoldResolutionError):
        await service.resolve_hold(
            decision_id=approved.decision_id,
            outcome=DecisionOutcome.DENY,
            resolved_by="usr_security_officer_07",
        )


@pytest.mark.asyncio
async def test_a_hold_cannot_be_decided_twice(memory_store, fake_nokia_client):
    """A second verdict would make the audit trail ambiguous about what stands."""
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    held, _ = await service.authorize_transaction(tx("tx_hold_4", "250000.00", 3, "HIGH_VALUE"))

    await service.resolve_hold(
        decision_id=held.decision_id,
        outcome=DecisionOutcome.APPROVE,
        resolved_by="usr_cargo_supervisor_02",
    )

    with pytest.raises(HoldResolutionError) as raised:
        await service.resolve_hold(
            decision_id=held.decision_id,
            outcome=DecisionOutcome.DENY,
            resolved_by="usr_security_officer_07",
        )

    assert "already resolved" in str(raised.value)


@pytest.mark.asyncio
async def test_a_human_may_not_answer_hold(memory_store, fake_nokia_client):
    """Holding again would leave the request parked with nobody accountable."""
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    held, _ = await service.authorize_transaction(tx("tx_hold_5", "250000.00", 3, "HIGH_VALUE"))

    with pytest.raises(HoldResolutionError) as raised:
        await service.resolve_hold(
            decision_id=held.decision_id,
            outcome=DecisionOutcome.HOLD,
            resolved_by="usr_cargo_supervisor_02",
        )

    assert "APPROVE or DENY" in str(raised.value)


@pytest.mark.asyncio
async def test_resolving_an_unknown_decision_is_reported_as_not_found(
    memory_store, fake_nokia_client
):
    service = build_service(memory_store, fake_nokia_client)

    with pytest.raises(DecisionNotFoundError):
        await service.resolve_hold(
            decision_id="dec_does_not_exist",
            outcome=DecisionOutcome.APPROVE,
            resolved_by="usr_cargo_supervisor_02",
        )


@pytest.mark.asyncio
async def test_the_issued_token_is_scoped_to_the_held_transaction(
    memory_store, fake_nokia_client
):
    """A token that is not bound to this exact act could be replayed elsewhere."""
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    held, _ = await service.authorize_transaction(tx("tx_hold_6", "250000.00", 3, "HIGH_VALUE"))

    _, receipt = await service.resolve_hold(
        decision_id=held.decision_id,
        outcome=DecisionOutcome.APPROVE,
        resolved_by="usr_cargo_supervisor_02",
    )

    assert receipt.token is not None
    assert receipt.transaction_id == "tx_hold_6"
    assert receipt.decision_id == held.decision_id


@pytest.mark.asyncio
async def test_the_resolution_is_durable_across_a_reread(
    memory_store, fake_nokia_client
):
    """The console reads the decision back; the verdict has to be there."""
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    held, _ = await service.authorize_transaction(tx("tx_hold_7", "250000.00", 3, "HIGH_VALUE"))
    await service.resolve_hold(
        decision_id=held.decision_id,
        outcome=DecisionOutcome.APPROVE,
        resolved_by="usr_cargo_supervisor_02",
        note="Cleared by radio",
    )

    reread = await memory_store.get_decision(held.decision_id)

    assert reread is not None
    assert reread.resolution is not None
    assert reread.resolution.note == "Cleared by radio"


@pytest.mark.asyncio
async def test_a_resolved_hold_leaves_the_pending_queue(
    memory_store, fake_nokia_client
):
    """The queue is what an operator works from; a decided item must drop out."""
    service = build_service(memory_store, fake_nokia_client)
    await seed_actor(memory_store)

    first, _ = await service.authorize_transaction(tx("tx_hold_8", "250000.00", 3, "HIGH_VALUE"))
    second, _ = await service.authorize_transaction(tx("tx_hold_9", "300000.00", 4, "HIGH_VALUE"))

    holds = await memory_store.list_decisions(outcome="HOLD", limit=100)
    pending = [d for d in holds if d.resolution is None]
    assert len(pending) == 2

    await service.resolve_hold(
        decision_id=first.decision_id,
        outcome=DecisionOutcome.APPROVE,
        resolved_by="usr_cargo_supervisor_02",
    )

    holds = await memory_store.list_decisions(outcome="HOLD", limit=100)
    pending = [d for d in holds if d.resolution is None]

    assert [d.decision_id for d in pending] == [second.decision_id]
