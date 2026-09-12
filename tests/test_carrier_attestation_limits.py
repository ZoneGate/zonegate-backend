"""What happens when the carrier cannot answer a check at all.

The live Nokia gateway cannot attest the number on a device from a server:
CAMARA identifies the subscriber from a token minted over the device's own
mobile connection. Declaring that check mandatory anyway meant every live
release was denied on evidence the deployment could never gather.

The fix is narrow on purpose, and these tests exist to keep it narrow. A
carrier that *can* answer a check is still held to it; a check that comes back
False still denies in every mode; and a decision reached without an
attestation says so on its face, so no approval can be read as resting on more
network evidence than it does.
"""

import pytest

from zonegate.domain.actors import Actor
from zonegate.domain.decisions import DecisionOutcome
from zonegate.domain.evidence import CanonicalEvidence, EvidenceKind, EvidencePlan
from zonegate.domain.transactions import TransactionRequest
from zonegate.evidence.plan_validator import EvidencePlanValidator
from zonegate.integrations.nokia.client import NokiaEvidenceClient
from zonegate.integrations.nokia.live import NokiaLiveEvidenceClient
from zonegate.policy.engine import PolicyEngine

WITHOUT_NUMBER = frozenset(EvidenceKind) - {EvidenceKind.NUMBER_VERIFICATION}


def actor() -> Actor:
    return Actor(
        actor_id="usr_cargo_operator_01",
        role="ROLE_CARGO_OPERATOR",
        permissions=["cargo:release"],
        registered_phone_number="+99999991001",
        registered_device_id="dev_imei_99887766",
        enrollment_status="ACTIVE",
    )


def transaction(**overrides) -> TransactionRequest:
    from datetime import datetime, timezone

    fields = {
        "transaction_id": "tx_attest",
        "actor_id": "usr_cargo_operator_01",
        "action": "RELEASE_CARGO",
        "resource_id": "CT-700",
        "zone": "PORT_GATE_17",
        "timestamp": datetime(2026, 9, 12, 14, 0, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return TransactionRequest(**fields)


def evidence(**overrides) -> CanonicalEvidence:
    from datetime import datetime, timezone

    fields = {
        "number_verified": None,
        "location_verified": True,
        "recent_sim_swap": False,
        "recent_device_swap": False,
        "reachable": True,
        "collected_at": datetime(2026, 9, 12, 14, 0, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return CanonicalEvidence(**fields)


class TestTheClientsDeclareWhatTheyCanAttest:
    def test_the_live_gateway_does_not_claim_number_verification(self):
        client = NokiaLiveEvidenceClient(api_key="k")

        assert EvidenceKind.NUMBER_VERIFICATION not in client.attestable_kinds
        # The other four are collectible and are not exempted along with it.
        assert EvidenceKind.LOCATION_VERIFICATION in client.attestable_kinds
        assert EvidenceKind.SIM_SWAP in client.attestable_kinds
        assert EvidenceKind.DEVICE_SWAP in client.attestable_kinds
        assert EvidenceKind.REACHABILITY in client.attestable_kinds

    def test_a_conformant_rest_endpoint_is_held_to_everything(self):
        client = NokiaEvidenceClient(base_url="http://mock", api_key="k")

        assert client.attestable_kinds == frozenset(EvidenceKind)


class TestTheRuleIsNotSwitchedOff:
    def test_a_carrier_that_can_answer_is_still_denied_for_not_answering(self):
        """The original invariant, unchanged: not collected is not a pass."""
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(number_verified=None),
            attestable=frozenset(EvidenceKind),
        )

        assert decision.decision == DecisionOutcome.DENY
        assert "not collected" in decision.reasons[0].lower()

    def test_the_default_is_still_the_strict_reading(self):
        # Callers that say nothing about attestation must not get the lenient
        # path by accident.
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(number_verified=None),
        )

        assert decision.decision == DecisionOutcome.DENY

    def test_a_carrier_saying_no_denies_even_where_it_is_unattestable(self):
        """A contradicted number is never waved through.

        The exemption covers a check the carrier cannot answer. It must not
        extend to one it answered with a refusal -- otherwise the same
        deployment that cannot verify numbers would also ignore the carrier
        telling it the number is wrong.
        """
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(number_verified=False),
            attestable=WITHOUT_NUMBER,
        )

        assert decision.decision == DecisionOutcome.DENY
        assert "failed" in decision.reasons[0].lower()

    def test_the_other_rules_still_bite_without_number_verification(self):
        """Dropping one attestation must not soften the rest."""
        outside_zone = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(location_verified=False),
            attestable=WITHOUT_NUMBER,
        )
        swapped = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(recent_sim_swap=True),
            attestable=WITHOUT_NUMBER,
        )
        no_permission = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor().model_copy(update={"permissions": []}),
            evidence=evidence(),
            attestable=WITHOUT_NUMBER,
        )

        assert outside_zone.decision == DecisionOutcome.DENY
        assert swapped.decision == DecisionOutcome.HOLD
        assert no_permission.decision == DecisionOutcome.DENY


class TestASilentlyDecliningCarrierIsNotAPassingOne:
    """The hole this closes was reachable from the demo account.

    The carrier declines a check for a subscriber it does not know, which the
    gateway records as not collected. With only number verification policed,
    an unknown number produced an APPROVE resting on no network evidence at
    all -- a green verdict that had verified nothing.
    """

    def test_a_mandatory_check_the_carrier_did_not_answer_denies(self):
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(location_verified=None),
            attestable=WITHOUT_NUMBER,
            required=frozenset({EvidenceKind.LOCATION_VERIFICATION}),
        )

        assert decision.decision == DecisionOutcome.DENY
        assert "not collected" in decision.reasons[0].lower()
        assert "location" in decision.reasons[0].lower()

    def test_an_empty_evidence_set_can_never_approve(self):
        """Nothing collected is the case that must not read as everything fine."""
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(
                location_verified=None,
                recent_sim_swap=None,
                recent_device_swap=None,
                reachable=None,
            ),
            attestable=WITHOUT_NUMBER,
            required=frozenset({EvidenceKind.LOCATION_VERIFICATION}),
        )

        assert decision.decision == DecisionOutcome.DENY

    def test_an_unplanned_check_is_not_treated_as_a_missing_one(self):
        """Only the plan's mandatory set is policed.

        SIM swap absent from the plan means nobody asked for it, which is not
        the same as asking and getting nothing, and must not deny.
        """
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(recent_sim_swap=None, reachable=None),
            attestable=WITHOUT_NUMBER,
            required=frozenset({EvidenceKind.LOCATION_VERIFICATION}),
        )

        assert decision.decision == DecisionOutcome.APPROVE

    def test_every_mandatory_kind_is_policed_not_just_the_first(self):
        for kind, field in [
            (EvidenceKind.LOCATION_VERIFICATION, "location_verified"),
            (EvidenceKind.SIM_SWAP, "recent_sim_swap"),
            (EvidenceKind.DEVICE_SWAP, "recent_device_swap"),
            (EvidenceKind.REACHABILITY, "reachable"),
        ]:
            decision = PolicyEngine().evaluate(
                transaction=transaction(),
                actor=actor(),
                evidence=evidence(**{field: None}),
                attestable=WITHOUT_NUMBER,
                required=frozenset({kind}),
            )

            assert decision.decision == DecisionOutcome.DENY, kind


class TestTheDecisionAdmitsWhatItRestsOn:
    def test_an_approval_without_the_attestation_carries_the_caveat(self):
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(),
            attestable=WITHOUT_NUMBER,
        )

        assert decision.decision == DecisionOutcome.APPROVE
        caveats = [r for r in decision.reasons if r.startswith("Caveat:")]
        assert len(caveats) == 1
        assert "enrolment record" in caveats[0]

    def test_a_hold_carries_it_too(self):
        # A human being asked to approve needs to know what the machine could
        # not check, not just why it stopped.
        decision = PolicyEngine().evaluate(
            transaction=transaction(category="WEAPONS"),
            actor=actor(),
            evidence=evidence(),
            attestable=WITHOUT_NUMBER,
        )

        assert decision.decision == DecisionOutcome.HOLD
        assert any(r.startswith("Caveat:") for r in decision.reasons)

    def test_a_permission_denial_carries_no_caveat(self):
        # The refusal is about the actor, not the network. A carrier caveat
        # under it reads as though the carrier had a hand in the outcome.
        stripped = actor().model_copy(update={"permissions": []})
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=stripped,
            evidence=evidence(),
            attestable=WITHOUT_NUMBER,
        )

        assert decision.decision == DecisionOutcome.DENY
        assert decision.reasons == [decision.reasons[0]]
        assert "permission" in decision.reasons[0]

    def test_an_evidence_denial_keeps_the_caveat(self):
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(location_verified=False),
            attestable=WITHOUT_NUMBER,
        )

        assert decision.decision == DecisionOutcome.DENY
        assert any(r.startswith("Caveat:") for r in decision.reasons)

    def test_a_full_carrier_adds_no_caveat(self):
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(number_verified=True),
            attestable=frozenset(EvidenceKind),
        )

        assert decision.decision == DecisionOutcome.APPROVE
        assert not any(r.startswith("Caveat:") for r in decision.reasons)

    def test_the_caveat_never_displaces_the_actual_reason(self):
        # reasons[0] is what every surface shows as the verdict line.
        decision = PolicyEngine().evaluate(
            transaction=transaction(),
            actor=actor(),
            evidence=evidence(location_verified=False),
            attestable=WITHOUT_NUMBER,
        )

        assert "geofence" in decision.reasons[0].lower()


class TestThePlanStopsAskingForIt:
    def test_an_unattestable_mandatory_check_is_dropped_from_the_plan(self):
        plan = EvidencePlanValidator(attestable=WITHOUT_NUMBER).validate(
            workflow_name="RELEASE_CARGO"
        )

        assert EvidenceKind.NUMBER_VERIFICATION not in plan.combined
        # The rest of the mandatory baseline is untouched.
        assert EvidenceKind.LOCATION_VERIFICATION in plan.mandatory

    def test_the_full_mandatory_baseline_survives_a_full_carrier(self):
        plan = EvidencePlanValidator(attestable=frozenset(EvidenceKind)).validate(
            workflow_name="RELEASE_CARGO"
        )

        assert EvidenceKind.NUMBER_VERIFICATION in plan.mandatory
        assert EvidenceKind.LOCATION_VERIFICATION in plan.mandatory

    def test_omitting_the_set_entirely_keeps_the_old_behaviour(self):
        plan = EvidencePlanValidator().validate(workflow_name="RELEASE_CARGO")

        assert EvidenceKind.NUMBER_VERIFICATION in plan.mandatory

    def test_the_agent_cannot_reinstate_a_check_the_carrier_cannot_answer(self):
        """The planner asking for it does not make it collectible."""
        validator = EvidencePlanValidator(
            attestable=frozenset({EvidenceKind.LOCATION_VERIFICATION})
        )

        plan = validator.validate(
            workflow_name="RELEASE_CARGO",
            proposed_plan=EvidencePlan(
                optional_evidence=[EvidenceKind.SIM_SWAP, EvidenceKind.REACHABILITY],
                rationale=["asking for everything"],
            ),
        )

        assert plan.combined == [EvidenceKind.LOCATION_VERIFICATION]

    def test_a_forbidden_kind_is_still_a_hard_failure(self):
        # Narrowing the plan must not turn a forbidden request into a quiet
        # no-op: proposing forbidden evidence is a validation failure.
        from zonegate.evidence.plan_validator import ForbiddenEvidenceError

        validator = EvidencePlanValidator(attestable=WITHOUT_NUMBER)

        with pytest.raises(ForbiddenEvidenceError):
            validator.validate(
                workflow_name="RELEASE_CARGO",
                proposed_plan=EvidencePlan(
                    optional_evidence=[EvidenceKind.KYC_MATCH],
                    rationale=["should be refused"],
                ),
            )
