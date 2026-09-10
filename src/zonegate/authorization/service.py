from datetime import datetime, timezone
import logging
from zonegate.agent.context_evaluator import ContextEvaluator
from zonegate.agent.evidence_planner import EvidencePlanner
from zonegate.agent.graph import build_context_evaluation_graph, build_evidence_planning_graph
from zonegate.authorization.token import TokenService
from zonegate.domain.decisions import (
    ContextEvaluation,
    DecisionOutcome,
    HoldResolution,
    PolicyDecision,
    RecommendedControl,
)
from zonegate.domain.evidence import CanonicalEvidence, ValidatedEvidencePlan
from zonegate.domain.receipts import Receipt
from zonegate.domain.transactions import TransactionRequest
from zonegate.evidence.gateway import ActorDeviceMismatchError, EvidenceGateway, EvidenceGatewayError
from zonegate.evidence.plan_validator import EvidencePlanValidationError, EvidencePlanValidator
from zonegate.evidence.workflow_policy import get_workflow_policy
from zonegate.policy.engine import PolicyEngine
from zonegate.storage.zova import ZoneGateStore

logger = logging.getLogger(__name__)


class AuthorizationServiceError(Exception):
    """Base exception for authorization service orchestration failures."""


class DecisionNotFoundError(AuthorizationServiceError):
    """Raised when a referenced policy decision does not exist."""


class HoldResolutionError(AuthorizationServiceError):
    """Raised when a decision is not eligible for human resolution."""


class AuthorizationService:
    """Core orchestrator for ZoneGate network-attested authorization pipeline.

    Orchestrates:
    1. Transaction ingestion and validation
    2. Identity & device binding lookup
    3. Workflow evidence policy resolution
    4. AI Evidence Planning (advisory)
    5. Deterministic evidence plan validation
    6. Runtime-authorized Nokia/CAMARA network evidence collection
    7. Canonical evidence normalization
    8. AI Context Evaluation (advisory)
    9. Deterministic Policy Engine evaluation (authoritative)
    10. State & receipt persistence in Zova 1.0.0-rc.3
    11. Scoped token generation (on APPROVE only)
    """

    def __init__(
        self,
        store: ZoneGateStore,
        gateway: EvidenceGateway,
        plan_validator: EvidencePlanValidator,
        policy_engine: PolicyEngine,
        token_service: TokenService,
        evidence_planner: EvidencePlanner | None = None,
        context_evaluator: ContextEvaluator | None = None,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.validator = plan_validator
        self.policy_engine = policy_engine
        self.token_service = token_service
        self.planner = evidence_planner
        self.evaluator = context_evaluator

        # Small LangGraph runners
        self._planning_graph = (
            build_evidence_planning_graph(evidence_planner) if evidence_planner else None
        )
        self._context_graph = (
            build_context_evaluation_graph(context_evaluator) if context_evaluator else None
        )

    async def resolve_hold(
        self,
        decision_id: str,
        outcome: DecisionOutcome,
        resolved_by: str,
        note: str = "",
    ) -> tuple[PolicyDecision, Receipt]:
        """Records the binding decision of the human authority a HOLD was handed to.

        The policy engine's own outcome is never rewritten: the stored decision stays
        HOLD and the human verdict is attached as a separate resolution record. Only a
        HOLD is eligible, so a deterministic DENY can never be overridden by a person.
        """
        decision = await self.store.get_decision(decision_id)
        if not decision:
            raise DecisionNotFoundError(f"Decision '{decision_id}' not found")

        if decision.decision != DecisionOutcome.HOLD:
            raise HoldResolutionError(
                f"Decision '{decision_id}' is {decision.decision} and is not open to human "
                "resolution. Only a HOLD transfers authority to a person."
            )

        if decision.resolution is not None:
            raise HoldResolutionError(
                f"Decision '{decision_id}' was already resolved as "
                f"{decision.resolution.outcome} by {decision.resolution.resolved_by}"
            )

        if outcome not in (DecisionOutcome.APPROVE, DecisionOutcome.DENY):
            raise HoldResolutionError(
                "A human resolution must be either APPROVE or DENY"
            )

        now = datetime.now(timezone.utc)
        resolution = HoldResolution(
            outcome=outcome,
            resolved_by=resolved_by,
            authority_role=decision.required_authority or "UNSPECIFIED_AUTHORITY",
            note=note,
            resolved_at=now,
        )

        resolved = decision.model_copy(update={"resolution": resolution})
        await self.store.save_decision(resolved)

        receipt = await self.store.get_receipt_by_transaction_id(decision.transaction_id)
        if receipt is None:
            receipt = Receipt(
                receipt_id=f"rcpt_{decision.decision_id}",
                decision_id=decision.decision_id,
                transaction_id=decision.transaction_id,
                decision=decision.decision,
                issued_at=now,
                token=None,
            )

        token_str = receipt.token
        if outcome == DecisionOutcome.APPROVE:
            transaction = await self.store.get_transaction(decision.transaction_id)
            if transaction is None:
                raise HoldResolutionError(
                    f"Transaction '{decision.transaction_id}' is no longer on record"
                )

            scoped_token = self.token_service.issue_token(
                actor_id=transaction.actor_id,
                action=transaction.action,
                resource_id=transaction.resource_id,
                zone=transaction.zone,
                decision_id=decision.decision_id,
            )
            token_str = scoped_token.signature

        updated_receipt = receipt.model_copy(
            update={"issued_at": now, "token": token_str}
        )
        await self.store.save_receipt(updated_receipt)

        logger.info(
            "HOLD %s resolved as %s by %s (%s)",
            decision.decision_id,
            outcome,
            resolved_by,
            resolution.authority_role,
        )
        return resolved, updated_receipt

    async def authorize_transaction(
        self,
        transaction: TransactionRequest,
    ) -> tuple[PolicyDecision, Receipt]:
        now = datetime.now(timezone.utc)
        logger.info(
            "Starting authorization pipeline for transaction=%s, actor=%s, action=%s, value=%s",
            transaction.transaction_id,
            transaction.actor_id,
            transaction.action,
            transaction.value,
        )

        # 1. Save transaction in Zova
        await self.store.save_transaction(transaction)

        # 2. Load Actor
        actor = await self.store.get_actor(transaction.actor_id)
        if not actor:
            logger.warning("Actor '%s' not registered", transaction.actor_id)
            decision = PolicyDecision(
                decision_id=f"dec_denied_{transaction.transaction_id}",
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.DENY,
                reasons=[f"Actor '{transaction.actor_id}' is not enrolled in ZoneGate"],
                required_authority=None,
                decided_at=now,
            )
            receipt = Receipt(
                receipt_id=f"rcpt_{decision.decision_id}",
                decision_id=decision.decision_id,
                transaction_id=transaction.transaction_id,
                decision=decision.decision,
                issued_at=now,
                token=None,
            )
            await self.store.save_decision(decision)
            await self.store.save_receipt(receipt)
            return decision, receipt

        # 3. Load Actor-Device Binding
        binding = await self.store.get_device_binding(transaction.actor_id)
        if not binding:
            logger.warning("No active device binding for actor '%s'", transaction.actor_id)
            decision = PolicyDecision(
                decision_id=f"dec_denied_{transaction.transaction_id}",
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.DENY,
                reasons=[f"No verified device binding found for actor '{transaction.actor_id}'"],
                required_authority=None,
                decided_at=now,
            )
            receipt = Receipt(
                receipt_id=f"rcpt_{decision.decision_id}",
                decision_id=decision.decision_id,
                transaction_id=transaction.transaction_id,
                decision=decision.decision,
                issued_at=now,
                token=None,
            )
            await self.store.save_decision(decision)
            await self.store.save_receipt(receipt)
            return decision, receipt

        # 4. Resolve Workflow Evidence Policy
        workflow_name = transaction.action  # e.g., "RELEASE_CARGO"
        policy = get_workflow_policy(workflow_name)

        # 5. AI Evidence Planning (Advisory via LangGraph)
        proposed_plan = None
        if self._planning_graph:
            try:
                state = await self._planning_graph.ainvoke({
                    "transaction": transaction,
                    "workflow_name": workflow_name,
                    "allowed_optional": list(policy.optional),
                })
                proposed_plan = state.get("evidence_plan")
            except Exception as exc:
                logger.warning("AI evidence planning failed safely, falling back to mandatory baseline: %s", exc)

        # 6. Validate Evidence Plan
        try:
            validated_plan: ValidatedEvidencePlan = self.validator.validate(
                workflow_name=workflow_name,
                proposed_plan=proposed_plan,
            )
        except EvidencePlanValidationError as exc:
            logger.error("Evidence plan validation failed: %s", exc)
            decision = PolicyDecision(
                decision_id=f"dec_denied_{transaction.transaction_id}",
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.DENY,
                reasons=[f"Evidence plan validation failed: {exc}"],
                decided_at=now,
            )
            receipt = Receipt(
                receipt_id=f"rcpt_{decision.decision_id}",
                decision_id=decision.decision_id,
                transaction_id=transaction.transaction_id,
                decision=decision.decision,
                issued_at=now,
                token=None,
            )
            await self.store.save_decision(decision)
            await self.store.save_receipt(receipt)
            return decision, receipt

        await self.store.save_evidence_plan(transaction.transaction_id, validated_plan)

        # 7 & 8. Collect Permitted Nokia/CAMARA Evidence through Gateway & Normalize
        try:
            canonical_evidence: CanonicalEvidence = await self.gateway.collect_evidence(
                actor=actor,
                binding=binding,
                transaction=transaction,
                plan=validated_plan,
            )
        except ActorDeviceMismatchError as exc:
            logger.warning("Actor-device binding verification failed: %s", exc)
            decision = PolicyDecision(
                decision_id=f"dec_denied_{transaction.transaction_id}",
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.DENY,
                reasons=[f"Device binding validation error: {exc}"],
                decided_at=now,
            )
            receipt = Receipt(
                receipt_id=f"rcpt_{decision.decision_id}",
                decision_id=decision.decision_id,
                transaction_id=transaction.transaction_id,
                decision=decision.decision,
                issued_at=now,
                token=None,
            )
            await self.store.save_decision(decision)
            await self.store.save_receipt(receipt)
            return decision, receipt
        except EvidenceGatewayError as exc:
            logger.error("Evidence gateway collection error: %s", exc)
            decision = PolicyDecision(
                decision_id=f"dec_denied_{transaction.transaction_id}",
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.DENY,
                reasons=[f"Network evidence collection failed: {exc}"],
                decided_at=now,
            )
            receipt = Receipt(
                receipt_id=f"rcpt_{decision.decision_id}",
                decision_id=decision.decision_id,
                transaction_id=transaction.transaction_id,
                decision=decision.decision,
                issued_at=now,
                token=None,
            )
            await self.store.save_decision(decision)
            await self.store.save_receipt(receipt)
            return decision, receipt

        await self.store.save_evidence(transaction.transaction_id, canonical_evidence)

        # 9. AI Context Evaluation (Advisory via LangGraph)
        context_evaluation: ContextEvaluation | None = None
        if self._context_graph:
            try:
                eval_state = await self._context_graph.ainvoke({
                    "transaction": transaction,
                    "canonical_evidence": canonical_evidence,
                })
                context_evaluation = eval_state.get("context_evaluation")
            except Exception as exc:
                logger.warning("AI context evaluation failed safely: %s", exc)
                context_evaluation = ContextEvaluation(
                    risk_factors=["AI_EVALUATION_UNAVAILABLE"],
                    recommended_control=RecommendedControl.HOLD,
                    rationale=f"AI context evaluator encountered an internal failure: {exc}",
                )

        if context_evaluation:
            await self.store.save_context_evaluation(transaction.transaction_id, context_evaluation)

        # 10. Deterministic Policy Engine Evaluation (LLM-free authoritative decision)
        decision: PolicyDecision = self.policy_engine.evaluate(
            transaction=transaction,
            actor=actor,
            evidence=canonical_evidence,
            context_evaluation=context_evaluation,
        )

        # 11. Scoped token issuance only on APPROVE
        token_str = None
        if decision.decision == DecisionOutcome.APPROVE:
            scoped_token = self.token_service.issue_token(
                actor_id=actor.actor_id,
                action=transaction.action,
                resource_id=transaction.resource_id,
                zone=transaction.zone,
                decision_id=decision.decision_id,
            )
            token_str = scoped_token.signature

        receipt = Receipt(
            receipt_id=f"rcpt_{decision.decision_id}",
            decision_id=decision.decision_id,
            transaction_id=transaction.transaction_id,
            decision=decision.decision,
            issued_at=now,
            token=token_str,
        )

        # Persist Decision and Receipt in Zova
        await self.store.save_decision(decision)
        await self.store.save_receipt(receipt)

        logger.info(
            "Authorization pipeline completed for tx=%s: decision=%s, reasons=%s",
            transaction.transaction_id,
            decision.decision,
            decision.reasons,
        )
        return decision, receipt
