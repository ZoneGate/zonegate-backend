"""Coverage of the two components that stand between intent and action.

The Evidence Gateway is the only thing allowed to call the carrier, and it
refuses to do so unless the actor and the device in front of it are the pair
that was enrolled. The Token Service mints the credential that says a specific
act was authorized — scoped so tightly that it is worthless anywhere else.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from zonegate.authorization.token import TokenService
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.evidence import EvidenceKind, ValidatedEvidencePlan
from zonegate.domain.transactions import TransactionRequest
from zonegate.evidence.gateway import (
    ActorDeviceMismatchError,
    EvidenceGateway,
    UnauthorizedEvidenceRequestError,
)


def actor(status: str = "ACTIVE") -> Actor:
    return Actor(
        actor_id="usr_cargo_operator_01",
        role="ROLE_CARGO_OPERATOR",
        permissions=["cargo:release"],
        registered_phone_number="+14155550199",
        registered_device_id="dev_imei_99887766",
        enrollment_status=status,
    )


def binding(**overrides) -> DeviceBinding:
    fields = {
        "actor_id": "usr_cargo_operator_01",
        "phone_number": "+14155550199",
        "device_id": "dev_imei_99887766",
        "bound_at": datetime.now(timezone.utc),
        "is_active": True,
    }
    fields.update(overrides)
    return DeviceBinding(**fields)


def transaction(zone: str = "PORT_GATE_17") -> TransactionRequest:
    return TransactionRequest(
        transaction_id="tx_gateway",
        actor_id="usr_cargo_operator_01",
        action="RELEASE_CARGO",
        resource_id="CT-800",
        zone=zone,
        timestamp=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc),
        value=Decimal("15000.00"),
    )


def plan(*kinds: EvidenceKind) -> ValidatedEvidencePlan:
    selected = list(kinds) or [EvidenceKind.NUMBER_VERIFICATION]
    return ValidatedEvidencePlan(
        mandatory=[EvidenceKind.NUMBER_VERIFICATION],
        optional=[k for k in selected if k != EvidenceKind.NUMBER_VERIFICATION],
        combined=selected,
        rationale=["test plan"],
    )


# ---------------------------------------------------------------------------
# Binding enforcement — the gate before the carrier is ever called
# ---------------------------------------------------------------------------


def test_a_matching_actor_and_binding_pass(fake_nokia_client):
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    gateway.validate_actor_device_binding(actor(), binding())


def test_a_binding_for_a_different_actor_is_rejected(fake_nokia_client):
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    with pytest.raises(ActorDeviceMismatchError) as raised:
        gateway.validate_actor_device_binding(
            actor(), binding(actor_id="usr_someone_else")
        )

    assert "does not match" in str(raised.value)


def test_a_number_that_is_not_the_enrolled_one_is_rejected(fake_nokia_client):
    """Otherwise an attacker's SIM could be attested in the victim's name."""
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    with pytest.raises(ActorDeviceMismatchError) as raised:
        gateway.validate_actor_device_binding(
            actor(), binding(phone_number="+14155550000")
        )

    assert "phone number" in str(raised.value).lower()


def test_a_device_that_is_not_the_enrolled_one_is_rejected(fake_nokia_client):
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    with pytest.raises(ActorDeviceMismatchError) as raised:
        gateway.validate_actor_device_binding(
            actor(), binding(device_id="dev_imei_00000000")
        )

    assert "device id" in str(raised.value).lower()


def test_an_inactive_binding_is_rejected(fake_nokia_client):
    """Revoking a binding has to stop authorization, not merely flag it."""
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    with pytest.raises(ActorDeviceMismatchError) as raised:
        gateway.validate_actor_device_binding(actor(), binding(is_active=False))

    assert "inactive" in str(raised.value)


@pytest.mark.parametrize("status", ["SUSPENDED", "REVOKED", "PENDING"])
def test_an_actor_who_is_not_active_is_rejected(status, fake_nokia_client):
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    with pytest.raises(ActorDeviceMismatchError) as raised:
        gateway.validate_actor_device_binding(actor(status=status), binding())

    assert status in str(raised.value)


@pytest.mark.asyncio
async def test_no_carrier_call_is_made_when_the_binding_fails(fake_nokia_client):
    """The binding check must come first, or a mismatch still leaks a lookup."""
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    with pytest.raises(ActorDeviceMismatchError):
        await gateway.collect_evidence(
            actor=actor(),
            binding=binding(is_active=False),
            transaction=transaction(),
            plan=plan(EvidenceKind.NUMBER_VERIFICATION),
        )

    assert fake_nokia_client.calls == []


# ---------------------------------------------------------------------------
# Collection is bounded by the plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_planned_checks_are_requested(fake_nokia_client):
    """A check nobody planned is an unbudgeted call on someone's subscriber."""
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    evidence = await gateway.collect_evidence(
        actor=actor(),
        binding=binding(),
        transaction=transaction(),
        plan=plan(EvidenceKind.NUMBER_VERIFICATION),
    )

    assert len(fake_nokia_client.calls) == 1
    assert fake_nokia_client.calls[0].startswith("verify_number")
    assert evidence.number_verified is True
    assert evidence.location_verified is None
    assert evidence.recent_sim_swap is None


@pytest.mark.asyncio
async def test_an_unplanned_check_reads_back_as_not_collected(fake_nokia_client):
    """`None` is the honest answer; False would read as a check that failed."""
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)
    fake_nokia_client.sim_swapped = True

    evidence = await gateway.collect_evidence(
        actor=actor(),
        binding=binding(),
        transaction=transaction(),
        plan=plan(EvidenceKind.NUMBER_VERIFICATION),
    )

    assert evidence.recent_sim_swap is None


@pytest.mark.asyncio
async def test_a_full_plan_collects_every_kind(fake_nokia_client):
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    evidence = await gateway.collect_evidence(
        actor=actor(),
        binding=binding(),
        transaction=transaction(),
        plan=plan(
            EvidenceKind.NUMBER_VERIFICATION,
            EvidenceKind.LOCATION_VERIFICATION,
            EvidenceKind.SIM_SWAP,
            EvidenceKind.DEVICE_SWAP,
        ),
    )

    assert evidence.number_verified is True
    assert evidence.location_verified is True
    assert evidence.recent_sim_swap is False
    assert evidence.recent_device_swap is False


@pytest.mark.asyncio
async def test_a_carrier_false_reading_is_carried_through_faithfully(
    fake_nokia_client,
):
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)
    fake_nokia_client.location_verified = False

    evidence = await gateway.collect_evidence(
        actor=actor(),
        binding=binding(),
        transaction=transaction(),
        plan=plan(
            EvidenceKind.NUMBER_VERIFICATION, EvidenceKind.LOCATION_VERIFICATION
        ),
    )

    assert evidence.location_verified is False


@pytest.mark.asyncio
async def test_the_carrier_is_asked_about_the_bound_number_not_the_actor_record(
    fake_nokia_client,
):
    """The binding is the authority on which subscriber to attest."""
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    await gateway.collect_evidence(
        actor=actor(),
        binding=binding(),
        transaction=transaction(),
        plan=plan(EvidenceKind.NUMBER_VERIFICATION),
    )

    assert "+14155550199" in fake_nokia_client.calls[0]


@pytest.mark.asyncio
async def test_an_unknown_zone_falls_back_to_the_default_geofence(
    fake_nokia_client,
):
    """An unmapped zone must still be checked, not silently skipped."""
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    await gateway.collect_evidence(
        actor=actor(),
        binding=binding(),
        transaction=transaction(zone="ZONE_NOBODY_REGISTERED"),
        plan=plan(
            EvidenceKind.NUMBER_VERIFICATION, EvidenceKind.LOCATION_VERIFICATION
        ),
    )

    location_calls = [c for c in fake_nokia_client.calls if c.startswith("verify_location")]
    assert len(location_calls) == 1


@pytest.mark.asyncio
async def test_an_unsupported_evidence_kind_is_refused(fake_nokia_client):
    """The allowlist is the gateway's own boundary, independent of the planner."""
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    with pytest.raises(UnauthorizedEvidenceRequestError):
        await gateway.collect_evidence(
            actor=actor(),
            binding=binding(),
            transaction=transaction(),
            plan=ValidatedEvidencePlan(
                mandatory=[EvidenceKind.NUMBER_VERIFICATION],
                optional=[EvidenceKind.KYC_MATCH],
                combined=[
                    EvidenceKind.NUMBER_VERIFICATION,
                    EvidenceKind.KYC_MATCH,
                ],
                rationale=["deliberately over-broad"],
            ),
        )


# ---------------------------------------------------------------------------
# Scoped authorization tokens
# ---------------------------------------------------------------------------


def test_a_token_binds_every_dimension_of_the_act_it_authorizes():
    token = TokenService().issue_token(
        actor_id="usr_cargo_operator_01",
        action="RELEASE_CARGO",
        resource_id="CT-800",
        zone="PORT_GATE_17",
        decision_id="dec_123",
    )

    assert token.actor_id == "usr_cargo_operator_01"
    assert token.action == "RELEASE_CARGO"
    assert token.resource_id == "CT-800"
    assert token.zone == "PORT_GATE_17"
    assert token.decision_id == "dec_123"


def test_each_token_is_single_use_by_identity():
    service = TokenService()

    ids = {
        service.issue_token(
            actor_id="usr_1",
            action="RELEASE_CARGO",
            resource_id="CT-1",
            zone="Z",
            decision_id="dec_1",
        ).token_id
        for _ in range(20)
    }

    assert len(ids) == 20


def test_a_token_expires(monkeypatch):
    token = TokenService().issue_token(
        actor_id="usr_1",
        action="RELEASE_CARGO",
        resource_id="CT-1",
        zone="Z",
        decision_id="dec_1",
        ttl_seconds=300,
    )

    assert (token.expires_at - token.issued_at).total_seconds() == 300


def test_changing_any_bound_field_changes_the_signature():
    """A signature that survived a field swap would let a token be repointed."""
    service = TokenService()

    base = service.issue_token(
        actor_id="usr_1",
        action="RELEASE_CARGO",
        resource_id="CT-1",
        zone="Z",
        decision_id="dec_1",
    )
    other = service.issue_token(
        actor_id="usr_1",
        action="RELEASE_CARGO",
        resource_id="CT-2",
        zone="Z",
        decision_id="dec_1",
    )

    assert base.signature != other.signature


def test_two_services_with_different_secrets_do_not_agree():
    """A token minted elsewhere must not verify here."""
    first = TokenService(secret_key="secret-one")
    second = TokenService(secret_key="secret-two")

    args = dict(
        actor_id="usr_1",
        action="RELEASE_CARGO",
        resource_id="CT-1",
        zone="Z",
        decision_id="dec_1",
    )

    # Same inputs, same issuing second — only the secret differs.
    a = first.issue_token(**args)
    b = second.issue_token(**args)

    assert a.signature != b.signature
