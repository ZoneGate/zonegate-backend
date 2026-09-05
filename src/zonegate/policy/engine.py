from datetime import datetime, timezone
from decimal import Decimal
import uuid
from zonegate.domain.actors import Actor
from zonegate.domain.decisions import (
    ContextEvaluation,
    DecisionOutcome,
    PolicyDecision,
)
from zonegate.domain.evidence import CanonicalEvidence
from zonegate.domain.transactions import TransactionRequest

# Thresholds
HIGH_VALUE_THRESHOLD = Decimal("100000.00")

# Action-to-permission mapping
ACTION_PERMISSIONS: dict[str, str] = {
    "RELEASE_CARGO": "cargo:release",
}


class PolicyEngine:
    """Completely LLM-free deterministic policy engine.

    The AI context evaluation is advisory and may contribute structured context,
    but cannot dictate or override policy decisions.
    """

    def __init__(
        self,
        high_value_threshold: Decimal = HIGH_VALUE_THRESHOLD,
    ) -> None:
        self.high_value_threshold = high_value_threshold

    def is_outside_expected_window(self, timestamp: datetime) -> bool:
        """Determines if the timestamp falls outside standard operational window (06:00 - 20:00 local/UTC)."""
        return timestamp.hour < 6 or timestamp.hour >= 20

    def evaluate(
        self,
        transaction: TransactionRequest,
        actor: Actor,
        evidence: CanonicalEvidence,
        context_evaluation: ContextEvaluation | None = None,
    ) -> PolicyDecision:
        reasons: list[str] = []
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        evidence_summary = {
            "number_verified": evidence.number_verified,
            "location_verified": evidence.location_verified,
            "recent_sim_swap": evidence.recent_sim_swap,
            "recent_device_swap": evidence.recent_device_swap,
            "reachable": evidence.reachable,
        }

        # Rule 1: Permission mismatch -> DENY
        required_perm = ACTION_PERMISSIONS.get(transaction.action, f"{transaction.action.lower()}:execute")
        if required_perm not in actor.permissions:
            reasons.append(f"Actor lacks required permission '{required_perm}' for action '{transaction.action}'")
            return PolicyDecision(
                decision_id=decision_id,
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.DENY,
                reasons=reasons,
                required_authority=None,
                context_evaluation=context_evaluation,
                evidence_summary=evidence_summary,
                decided_at=now,
            )

        # Rule 2: Number verification != True -> DENY
        if evidence.number_verified is not True:
            reasons.append("Subscriber number verification failed or was not verified")
            return PolicyDecision(
                decision_id=decision_id,
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.DENY,
                reasons=reasons,
                required_authority=None,
                context_evaluation=context_evaluation,
                evidence_summary=evidence_summary,
                decided_at=now,
            )

        # Rule 3: Location verification == False -> DENY
        if evidence.location_verified is False:
            reasons.append("Device location geofence check failed: device is outside authorized zone")
            return PolicyDecision(
                decision_id=decision_id,
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.DENY,
                reasons=reasons,
                required_authority=None,
                context_evaluation=context_evaluation,
                evidence_summary=evidence_summary,
                decided_at=now,
            )

        # Rule 4: High-value AND outside expected window -> HOLD
        is_high_val = transaction.value >= self.high_value_threshold
        outside_window = self.is_outside_expected_window(transaction.timestamp)

        if is_high_val and outside_window:
            reasons.append(
                f"High-value transaction (${transaction.value}) requested outside expected operational window ({transaction.timestamp.strftime('%H:%M')} UTC)"
            )
            return PolicyDecision(
                decision_id=decision_id,
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.HOLD,
                reasons=reasons,
                required_authority="ROLE_CARGO_SUPERVISOR",
                context_evaluation=context_evaluation,
                evidence_summary=evidence_summary,
                decided_at=now,
            )

        # Rule 5: Recent SIM swap AND high-value -> HOLD
        if evidence.recent_sim_swap is True and is_high_val:
            reasons.append(
                f"Recent carrier SIM swap detected on subscriber device for high-value transaction (${transaction.value})"
            )
            return PolicyDecision(
                decision_id=decision_id,
                transaction_id=transaction.transaction_id,
                decision=DecisionOutcome.HOLD,
                reasons=reasons,
                required_authority="ROLE_SECURITY_OFFICER",
                context_evaluation=context_evaluation,
                evidence_summary=evidence_summary,
                decided_at=now,
            )

        # Rule 6: Otherwise -> APPROVE
        reasons.append("All network identity and geofence verifications passed deterministic policy checks")
        return PolicyDecision(
            decision_id=decision_id,
            transaction_id=transaction.transaction_id,
            decision=DecisionOutcome.APPROVE,
            reasons=reasons,
            required_authority=None,
            context_evaluation=context_evaluation,
            evidence_summary=evidence_summary,
            decided_at=now,
        )
