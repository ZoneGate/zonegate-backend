from datetime import datetime, timezone
import uuid
from zonegate.domain.actors import Actor
from zonegate.domain.decisions import (
    ContextEvaluation,
    DecisionOutcome,
    PolicyDecision,
)
from zonegate.domain.evidence import CanonicalEvidence, EvidenceKind
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

# Which field on the canonical evidence each planned check lands in, so the
# engine can tell a check that was required and never answered from one that
# was simply not on the plan. KYC_MATCH is absent because the RELEASE_CARGO
# workflow forbids it outright.
EVIDENCE_FIELDS: dict[EvidenceKind, str] = {
    EvidenceKind.NUMBER_VERIFICATION: "number_verified",
    EvidenceKind.LOCATION_VERIFICATION: "location_verified",
    EvidenceKind.SIM_SWAP: "recent_sim_swap",
    EvidenceKind.DEVICE_SWAP: "recent_device_swap",
    EvidenceKind.REACHABILITY: "reachable",
}

EVIDENCE_LABELS: dict[EvidenceKind, str] = {
    EvidenceKind.NUMBER_VERIFICATION: "Subscriber number verification",
    EvidenceKind.LOCATION_VERIFICATION: "Device location verification",
    EvidenceKind.SIM_SWAP: "SIM continuity",
    EvidenceKind.DEVICE_SWAP: "Device continuity",
    EvidenceKind.REACHABILITY: "Network reachability",
}


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
        attestable: frozenset[EvidenceKind] | None = None,
        required: frozenset[EvidenceKind] | None = None,
    ) -> PolicyDecision:
        """Decide, given what this deployment's carrier is able to attest.

        `required` is the mandatory evidence of the validated plan -- the
        checks this decision is not entitled to be made without. Any of them
        missing is a denial, whatever the reason for the gap. Default None
        keeps the historical behaviour of only policing number verification.

        `attestable` is the set of checks the carrier can answer at all.
        Default None means all of them, which is the strictest reading and
        what a conformant operator endpoint supports.

        The two are different and the difference matters. A capability the
        subscription does not have is dropped from `required` by the plan
        validator and reported as a caveat here. A capability the carrier has
        but did not answer for *this* request -- an unknown number, a
        transient refusal -- is still required, and still denies. Otherwise a
        carrier that quietly declines everything would read as a carrier that
        verified everything.
        """
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        supported = attestable if attestable is not None else frozenset(EvidenceKind)

        # Caveats travel with the decision rather than being logged and lost.
        # An approval that rests on four network checks instead of five has to
        # say which one is missing, or it reads as the stronger thing.
        caveats: list[str] = []
        if EvidenceKind.NUMBER_VERIFICATION not in supported:
            caveats.append(
                "Caveat: this deployment's carrier cannot attest the number on the "
                "device, so the actor-to-number binding rests on the ZoneGate "
                "enrolment record rather than on network attestation"
            )

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
                reasons=[reason, *caveats],
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

        # Rule 2: the carrier contradicting the registered number -> DENY.
        # This fires whenever the check was answered, in every mode. A carrier
        # that says "this is not that number" is never overridable.
        if evidence.number_verified is False:
            return outcome(
                DecisionOutcome.DENY,
                "Subscriber number verification failed: the carrier did not confirm "
                "the registered number on this device",
            )

        # Rule 2b: mandatory evidence that never arrived -> DENY.
        #
        # Distinct from Rule 2 on purpose. "The carrier says this is not your
        # number" is a finding against the operator; "the check was required
        # and did not run" is a finding against the deployment, and only the
        # second is something the reader can go and fix.
        #
        # This is the rule that stops a silently declining carrier from
        # reading as a passing one. If the carrier refuses a check for this
        # particular subscriber -- a number it does not know, a transient
        # refusal -- the evidence is missing, and missing mandatory evidence
        # is never an approval. A capability the subscription does not have at
        # all is already absent from `required`, and says so in the caveat.
        mandatory = required if required is not None else frozenset(
            {EvidenceKind.NUMBER_VERIFICATION} & supported
        )
        for kind in sorted(mandatory, key=lambda k: k.value):
            field = EVIDENCE_FIELDS.get(kind)
            if field is not None and getattr(evidence, field) is None:
                return outcome(
                    DecisionOutcome.DENY,
                    f"{EVIDENCE_LABELS[kind]} is required for this release but was "
                    "not collected; the carrier did not answer the check, so the "
                    "release cannot rest on network evidence that does not exist",
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

        # Rule 7: Otherwise -> APPROVE.
        # The reason names the checks that actually passed rather than
        # claiming "all verifications passed". Written the old way, an
        # approval resting on one carrier answer still read as a full identity
        # and geofence clearance, which is the kind of sentence somebody
        # quotes in an incident review.
        passed = [
            EVIDENCE_LABELS[kind].lower()
            for kind in sorted(mandatory, key=lambda k: k.value)
            if kind in EVIDENCE_FIELDS
        ]
        checks = ", ".join(passed) if passed else "no network checks"
        return outcome(
            DecisionOutcome.APPROVE,
            f"Category '{transaction.category}' is unrestricted and the required "
            f"network evidence passed deterministic policy checks ({checks})",
        )
