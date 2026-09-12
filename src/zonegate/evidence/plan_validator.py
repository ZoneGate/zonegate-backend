import logging

from zonegate.domain.evidence import EvidenceKind, EvidencePlan, ValidatedEvidencePlan
from zonegate.evidence.workflow_policy import get_workflow_policy, WorkflowEvidencePolicy

logger = logging.getLogger(__name__)


class EvidencePlanValidationError(Exception):
    """Base exception for evidence plan validation failures."""


class ForbiddenEvidenceError(EvidencePlanValidationError):
    """Raised when an evidence plan proposes evidence that is forbidden for the workflow."""


class UnsupportedEvidenceError(EvidencePlanValidationError):
    """Raised when an evidence plan proposes evidence not permitted or recognized for the workflow."""


class EvidencePlanValidator:
    """Validates advisory AI evidence plans against deterministic workflow evidence policy.

    Guarantees:
    1. Mandatory baseline evidence is always enforced regardless of AI output.
    2. Proposing forbidden evidence immediately fails validation.
    3. AI can only add allowed optional evidence.

    `attestable` narrows the plan to what this deployment's carrier can answer
    at all. It is not a discretionary setting and the agent has no access to
    it: a check the carrier cannot answer is dropped from the plan rather than
    requested and silently missed, and the policy engine is told the same set
    so the decision states what it could not rest on. Omitting it means every
    check is available, which is the strictest reading.
    """

    def __init__(self, attestable: frozenset[EvidenceKind] | None = None) -> None:
        self.attestable = attestable

    def validate(
        self,
        workflow_name: str,
        proposed_plan: EvidencePlan | None = None,
    ) -> ValidatedEvidencePlan:
        policy: WorkflowEvidencePolicy = get_workflow_policy(workflow_name)

        validated_optional: list[EvidenceKind] = []
        rationale: list[str] = []
        proposed_optional: list[EvidenceKind] = []

        if proposed_plan is not None:
            rationale = list(proposed_plan.rationale)
            proposed_optional = list(proposed_plan.optional_evidence)
            for evidence in proposed_plan.optional_evidence:
                # 1. Check if explicitly forbidden
                if evidence in policy.forbidden:
                    raise ForbiddenEvidenceError(
                        f"Evidence kind '{evidence}' is strictly forbidden for workflow '{workflow_name}'"
                    )

                # 2. Check if allowed in optional set
                if evidence not in policy.optional:
                    raise UnsupportedEvidenceError(
                        f"Evidence kind '{evidence}' is not permitted as optional evidence for workflow '{workflow_name}'"
                    )

                if evidence not in validated_optional:
                    validated_optional.append(evidence)

        # Mandatory evidence is always included, never dependent on LLM.
        mandatory = set(policy.mandatory)
        if self.attestable is not None:
            unattestable = mandatory - self.attestable
            if unattestable:
                # Logged every time, because a deployment quietly collecting
                # less than its workflow policy demands is worth noticing in
                # the operator's logs and not only in the decision record.
                logger.warning(
                    "Workflow '%s' requires %s, which this carrier cannot attest; "
                    "dropped from the plan and reported as a caveat on every decision",
                    workflow_name,
                    ", ".join(sorted(k.value for k in unattestable)),
                )
                mandatory -= unattestable
            validated_optional = [k for k in validated_optional if k in self.attestable]

        mandatory_list = sorted(mandatory, key=lambda k: k.value)
        combined = list(dict.fromkeys(mandatory_list + validated_optional))

        return ValidatedEvidencePlan(
            mandatory=mandatory_list,
            optional=validated_optional,
            combined=combined,
            rationale=rationale,
            planner_consulted=proposed_plan is not None,
            offered_optional=sorted(policy.optional, key=lambda k: k.value),
            proposed_optional=proposed_optional,
        )
