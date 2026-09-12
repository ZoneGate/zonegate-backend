"""Pipeline progress, as it happens.

A release request runs six distinct steps against three different systems, and
until now the operator watched a spinner for all of them. These are the events
the pipeline emits as it goes, so a client can show which step it is on and,
when something is refused, which step refused it.

The names are the pipeline's own stages, not a decorative animation: an event
is emitted where the work actually happens, so a stage that finishes instantly
is reported instantly and one that waits on a carrier is reported as waiting.
"""

from enum import StrEnum
from typing import Awaitable, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Stage(StrEnum):
    IDENTITY = "IDENTITY"
    PLAN = "PLAN"
    VALIDATE = "VALIDATE"
    EVIDENCE = "EVIDENCE"
    CONTEXT = "CONTEXT"
    POLICY = "POLICY"


class StageStatus(StrEnum):
    STARTED = "STARTED"
    DONE = "DONE"
    #: Reached, but there was nothing to do — no agent attached, for instance.
    SKIPPED = "SKIPPED"
    #: This stage is where the request stopped. A decision still follows.
    FAILED = "FAILED"


#: What each stage is doing, in the words a field operator would use.
STAGE_LABELS: dict[Stage, str] = {
    Stage.IDENTITY: "Checking enrolment and device binding",
    Stage.PLAN: "Agent choosing which evidence to collect",
    Stage.VALIDATE: "Validating the plan against workflow policy",
    Stage.EVIDENCE: "Collecting network evidence from the carrier",
    Stage.CONTEXT: "Agent assessing risk from the evidence",
    Stage.POLICY: "Deterministic policy engine deciding",
}


class StageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Stage
    status: StageStatus
    label: str = Field(..., description="What this stage is doing, in plain words")
    detail: str = Field(default="", description="What actually happened, once known")


class StageListener(Protocol):
    """Receives each event as the pipeline reaches it."""

    def __call__(self, event: StageEvent) -> Awaitable[None]: ...


Emitter = Callable[[Stage, StageStatus, str], Awaitable[None]]


def make_emitter(listener: StageListener | None) -> Emitter:
    """An emitter that does nothing when nobody is listening.

    The non-streaming endpoint is the common case, and it must not pay for
    progress reporting or be able to fail because of it.
    """

    async def emit(stage: Stage, status: StageStatus, detail: str = "") -> None:
        if listener is None:
            return

        await listener(
            StageEvent(
                stage=stage,
                status=status,
                label=STAGE_LABELS[stage],
                detail=detail,
            )
        )

    return emit
