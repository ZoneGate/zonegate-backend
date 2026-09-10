from zonegate.domain.evidence import EvidenceKind, EvidencePlan, ValidatedEvidencePlan
from zonegate.evidence.workflow_policy import get_workflow_policy, WorkflowEvidencePolicy


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
    """

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

        # Mandatory evidence is always included, never dependent on LLM
        mandatory_list = sorted(list(policy.mandatory), key=lambda k: k.value)
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
