from datetime import datetime, timezone
import uuid
from zonegate.domain.actors import Actor
from zonegate.domain.decisions import (
    ContextEvaluation,
    DecisionOutcome,
    PolicyDecision,
)
from zonegate.domain.evidence import CanonicalEvidence
from zonegate.domain.policy_config import PolicyConfig
from zonegate.domain.transactions import TransactionRequest

# Action-to-permission mapping
ACTION_PERMISSIONS: dict[str, str] = {
    "RELEASE_CARGO": "cargo:release",
}

# The role an out-of-hours release answers to when its category is otherwise
# unrestricted. Restricted categories name their own authority in the config.
OFF_HOURS_AUTHORITY = "ROLE_CARGO_SUPERVISOR"
SIM_SWAP_AUTHORITY = "ROLE_SECURITY_OFFICER"


class PolicyEngine:
    """Completely LLM-free deterministic policy engine.

    The AI context evaluation is advisory and may contribute structured context,
    but cannot dictate or override policy decisions.
    """

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    def is_outside_expected_window(self, timestamp: datetime) -> bool:
        """Whether the timestamp falls outside the configured operational window."""
        return (
            timestamp.hour < self.config.window_start_hour
            or timestamp.hour >= self.config.window_end_hour
        )

    def evaluate(
        self,
        transaction: TransactionRequest,
        actor: Actor,
        evidence: CanonicalEvidence,
        context_evaluation: ContextEvaluation | None = None,
    ) -> PolicyDecision:
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        evidence_summary = {
            "number_verified": evidence.number_verified,
            "location_verified": evidence.location_verified,
            "recent_sim_swap": evidence.recent_sim_swap,
            "recent_device_swap": evidence.recent_device_swap,
            "reachable": evidence.reachable,
        }

        def outcome(
            decision: DecisionOutcome,
            reason: str,
            required_authority: str | None = None,
        ) -> PolicyDecision:
            return PolicyDecision(
                decision_id=decision_id,
                transaction_id=transaction.transaction_id,
                decision=decision,
                reasons=[reason],
                required_authority=required_authority,
                context_evaluation=context_evaluation,
                evidence_summary=evidence_summary,
                decided_at=now,
            )

        # Rule 1: Permission mismatch -> DENY
        required_perm = ACTION_PERMISSIONS.get(
            transaction.action, f"{transaction.action.lower()}:execute"
        )
        if required_perm not in actor.permissions:
            return outcome(
                DecisionOutcome.DENY,
                f"Actor lacks required permission '{required_perm}' for action '{transaction.action}'",
            )

        # Rule 2: Number verification != True -> DENY
        if evidence.number_verified is not True:
            return outcome(
                DecisionOutcome.DENY,
                "Subscriber number verification failed or was not verified",
            )

        # Rule 3: Location verification == False -> DENY
        if evidence.location_verified is False:
            return outcome(
                DecisionOutcome.DENY,
                "Device location geofence check failed: device is outside authorized zone",
            )

        # Rule 4: Recent SIM swap -> HOLD.
        # A swapped SIM means the line the evidence was collected over may no
        # longer be the operator's, so no category is safe to release on it.
        if evidence.recent_sim_swap is True:
            return outcome(
                DecisionOutcome.HOLD,
                "Recent carrier SIM swap detected on the subscriber device; the line "
                "the network evidence was collected over may have changed hands",
                SIM_SWAP_AUTHORITY,
            )

        # Rule 5: Restricted cargo category -> HOLD, to that category's authority.
        restricted_authority = self.config.authority_for(transaction.category)
        if restricted_authority:
            return outcome(
                DecisionOutcome.HOLD,
                f"Cargo category '{transaction.category}' is restricted and is released "
                f"only on the approval of {restricted_authority}",
                restricted_authority,
            )

        # Rule 6: Outside the expected operational window -> HOLD
        if self.is_outside_expected_window(transaction.timestamp):
            return outcome(
                DecisionOutcome.HOLD,
                f"Release requested at {transaction.timestamp.strftime('%H:%M')} UTC, outside the "
                f"expected operational window "
                f"({self.config.window_start_hour:02d}:00-{self.config.window_end_hour:02d}:00 UTC)",
                OFF_HOURS_AUTHORITY,
            )

        # Rule 7: Otherwise -> APPROVE
        return outcome(
            DecisionOutcome.APPROVE,
            f"Category '{transaction.category}' is unrestricted and all network identity "
            "and geofence verifications passed deterministic policy checks",
        )
