from datetime import datetime, timezone
from decimal import Decimal
import pytest
from zonegate.agent.ollama import OllamaClient, OllamaStructuredOutputError
from zonegate.authorization.service import AuthorizationService
from zonegate.authorization.token import TokenService
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.decisions import DecisionOutcome
from zonegate.domain.evidence import EvidenceKind, ValidatedEvidencePlan
from zonegate.domain.transactions import TransactionRequest
from zonegate.evidence.gateway import ActorDeviceMismatchError, EvidenceGateway
from zonegate.evidence.plan_validator import EvidencePlanValidator
from zonegate.policy.engine import PolicyEngine
from zonegate.storage.zova import ZoneGateStore


@pytest.mark.asyncio
async def test_evidence_gateway_rejects_actor_device_mismatch(
    standard_actor: Actor,
    standard_transaction: TransactionRequest,
    fake_nokia_client: FakeNokiaClient,
):
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    # Binding has a different phone number
    mismatched_binding = DeviceBinding(
        actor_id=standard_actor.actor_id,
        phone_number="+19999999999",  # mismatch!
        device_id=standard_actor.registered_device_id,
        bound_at=datetime.now(timezone.utc),
        is_active=True,
    )

    plan = ValidatedEvidencePlan(
        mandatory=[EvidenceKind.NUMBER_VERIFICATION, EvidenceKind.LOCATION_VERIFICATION],
        optional=[],
        combined=[EvidenceKind.NUMBER_VERIFICATION, EvidenceKind.LOCATION_VERIFICATION],
    )

    with pytest.raises(ActorDeviceMismatchError) as exc_info:
        await gateway.collect_evidence(
            actor=standard_actor,
            binding=mismatched_binding,
            transaction=standard_transaction,
            plan=plan,
        )

    assert "Registered phone number does not match device binding" in str(exc_info.value)
    # Ensure no calls were dispatched to Nokia
    assert len(fake_nokia_client.calls) == 0


@pytest.mark.asyncio
async def test_ai_selected_evidence_passes_through_gateway(
    standard_actor: Actor,
    standard_device_binding: DeviceBinding,
    standard_transaction: TransactionRequest,
    fake_nokia_client: FakeNokiaClient,
):
    gateway = EvidenceGateway(nokia_client=fake_nokia_client)

    # Validated plan contains mandatory + AI-selected optional SIM_SWAP
    plan = ValidatedEvidencePlan(
        mandatory=[EvidenceKind.NUMBER_VERIFICATION, EvidenceKind.LOCATION_VERIFICATION],
        optional=[EvidenceKind.SIM_SWAP],
        combined=[
            EvidenceKind.NUMBER_VERIFICATION,
            EvidenceKind.LOCATION_VERIFICATION,
            EvidenceKind.SIM_SWAP,
        ],
    )

    canonical = await gateway.collect_evidence(
        actor=standard_actor,
        binding=standard_device_binding,
        transaction=standard_transaction,
        plan=plan,
    )

    assert canonical.number_verified is True
    assert canonical.location_verified is True
    assert canonical.recent_sim_swap is False
    assert canonical.recent_device_swap is None  # not requested
    assert canonical.kyc_match is None  # forbidden/not requested

    # Check calls made to Nokia
    assert any("verify_number" in c for c in fake_nokia_client.calls)
    assert any("verify_location" in c for c in fake_nokia_client.calls)
    assert any("check_sim_swap" in c for c in fake_nokia_client.calls)
    assert not any("check_device_swap" in c for c in fake_nokia_client.calls)


@pytest.mark.asyncio
async def test_malformed_ai_structured_output_fails_safely():
    """AI returning malformed or unparseable output must raise explicit error and never approve."""
    # Create an OllamaClient pointing to an invalid port
    client = OllamaClient(base_url="http://127.0.0.1:59999", model="llama3.2")
    is_up = await client.check_health()
    assert is_up is False


@pytest.mark.asyncio
async def test_full_authorization_service_pipeline_and_zova_persistence(
    memory_store: ZoneGateStore,
    standard_actor: Actor,
    standard_device_binding: DeviceBinding,
    standard_transaction: TransactionRequest,
    fake_nokia_client: FakeNokiaClient,
):
    # Setup Zova state
    await memory_store.save_actor(standard_actor)
    await memory_store.save_device_binding(standard_device_binding)

    gateway = EvidenceGateway(nokia_client=fake_nokia_client)
    validator = EvidencePlanValidator()
    policy_engine = PolicyEngine()
    token_service = TokenService()

    service = AuthorizationService(
        store=memory_store,
        gateway=gateway,
        plan_validator=validator,
        policy_engine=policy_engine,
        token_service=token_service,
    )

    decision, receipt = await service.authorize_transaction(standard_transaction)

    assert decision.decision == DecisionOutcome.APPROVE
    assert receipt.decision == DecisionOutcome.APPROVE
    assert receipt.token is not None  # Scoped authorization token issued

    # Verify persisted in Zova
    saved_decision = await memory_store.get_decision(decision.decision_id)
    assert saved_decision is not None
    assert saved_decision.decision == DecisionOutcome.APPROVE
    assert saved_decision.transaction_id == standard_transaction.transaction_id

    saved_receipt = await memory_store.get_receipt(receipt.receipt_id)
    assert saved_receipt is not None
    assert saved_receipt.token == receipt.token

    # Verify scoped token is persisted in Zova registry
    saved_token = await memory_store.get_token(receipt.token)
    assert saved_token is not None
    assert saved_token.action == standard_transaction.action
    assert saved_token.resource_id == standard_transaction.resource_id
    assert saved_token.claimed is False
