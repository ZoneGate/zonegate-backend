from dataclasses import dataclass
from zonegate.domain.evidence import EvidenceKind


@dataclass(frozen=True)
class WorkflowEvidencePolicy:
    workflow_name: str
    mandatory: frozenset[EvidenceKind]
    optional: frozenset[EvidenceKind]
    forbidden: frozenset[EvidenceKind]


WORKFLOW_POLICIES: dict[str, WorkflowEvidencePolicy] = {
    "RELEASE_CARGO": WorkflowEvidencePolicy(
        workflow_name="RELEASE_CARGO",
        mandatory=frozenset({
            EvidenceKind.NUMBER_VERIFICATION,
            EvidenceKind.LOCATION_VERIFICATION,
        }),
        optional=frozenset({
            EvidenceKind.SIM_SWAP,
            EvidenceKind.DEVICE_SWAP,
            EvidenceKind.REACHABILITY,
        }),
        forbidden=frozenset({
            EvidenceKind.KYC_MATCH,
        }),
    ),
}


def get_workflow_policy(workflow_name: str) -> WorkflowEvidencePolicy:
    policy = WORKFLOW_POLICIES.get(workflow_name)
    if not policy:
        raise ValueError(f"No evidence policy configured for workflow: {workflow_name}")
    return policy
