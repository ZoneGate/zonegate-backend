import asyncio
import json
import logging
from typing import AsyncGenerator

from litestar import Controller, get, post
from litestar.di import NamedDependency
from litestar.exceptions import ClientException, NotFoundException
from litestar.params import FromPath, FromQuery
from litestar.response import ServerSentEvent, ServerSentEventMessage
from pydantic import BaseModel, ConfigDict, Field
from zonegate.authorization.progress import StageEvent
from zonegate.authorization.service import (
    AuthorizationService,
    DecisionNotFoundError,
    HoldResolutionError,
)
from zonegate.domain.decisions import DecisionOutcome, PolicyDecision
from zonegate.domain.evidence import CanonicalEvidence, ValidatedEvidencePlan
from zonegate.domain.receipts import Receipt
from zonegate.domain.transactions import TransactionRequest


class AuthorizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision
    receipt: Receipt


class HoldResolutionRequest(BaseModel):
    """Binding verdict from the human authority the policy engine handed a HOLD to."""
    model_config = ConfigDict(extra="forbid")

    outcome: DecisionOutcome = Field(..., description="Final human outcome: APPROVE or DENY")
    resolved_by: str = Field(..., description="Identifier of the deciding human authority")
    note: str = Field(default="", description="Justification recorded in the audit trail")


class DecisionContext(BaseModel):
    """Everything the human authority needs to decide a HOLD, in one payload."""
    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision
    transaction: TransactionRequest | None = None
    evidence: CanonicalEvidence | None = None
    evidence_plan: ValidatedEvidencePlan | None = None
    receipt: Receipt | None = None


logger = logging.getLogger(__name__)


class AuthorizationController(Controller):
    path = "/v1/authorizations"

    @post("/stream", status_code=200, media_type="text/event-stream")
    async def stream_authorization(
        self,
        data: TransactionRequest,
        auth_service: NamedDependency[AuthorizationService],
    ) -> ServerSentEvent:
        """The same pipeline as POST /v1/authorizations, reported as it runs.

        A release takes six steps against three systems, and a client that can
        only await the final answer has nothing to show for the wait but a
        spinner. Each `stage` event here is emitted where the work actually
        happens; the closing `result` event carries exactly the payload the
        plain endpoint returns, so a client can use either.
        """
        queue: asyncio.Queue[ServerSentEventMessage | None] = asyncio.Queue()

        async def on_stage(event: StageEvent) -> None:
            await queue.put(
                ServerSentEventMessage(event="stage", data=event.model_dump_json())
            )

        async def run() -> None:
            try:
                decision, receipt = await auth_service.authorize_transaction(
                    data, listener=on_stage
                )
                payload = AuthorizationResponse(decision=decision, receipt=receipt)
                await queue.put(
                    ServerSentEventMessage(event="result", data=payload.model_dump_json())
                )
            except Exception as exc:  # noqa: BLE001 - reported to the client, not swallowed
                logger.exception("Streamed authorization failed for tx=%s", data.transaction_id)
                await queue.put(
                    ServerSentEventMessage(
                        event="error",
                        data=json.dumps({"detail": str(exc)}),
                    )
                )
            finally:
                # The sentinel is what ends the stream; without it a client
                # that lost the pipeline would wait for a message forever.
                await queue.put(None)

        async def events() -> AsyncGenerator[ServerSentEventMessage, None]:
            task = asyncio.create_task(run())
            try:
                while True:
                    message = await queue.get()
                    if message is None:
                        break
                    yield message
            finally:
                # A client that hangs up mid-pipeline must not leave it running.
                if not task.done():
                    task.cancel()

        return ServerSentEvent(events())

    @post()
    async def request_authorization(
        self,
        data: TransactionRequest,
        auth_service: NamedDependency[AuthorizationService],
    ) -> AuthorizationResponse:
        """Evaluates a critical human action request through the network-attested pipeline."""
        decision, receipt = await auth_service.authorize_transaction(data)
        return AuthorizationResponse(decision=decision, receipt=receipt)

    @get()
    async def list_authorization_decisions(
        self,
        auth_service: NamedDependency[AuthorizationService],
        decision: FromQuery[DecisionOutcome | None] = None,
        pending: FromQuery[bool] = False,
        limit: FromQuery[int] = 100,
    ) -> list[PolicyDecision]:
        """Lists policy decisions newest first.

        `decision` filters by outcome; `pending=true` narrows a HOLD listing to the
        decisions still awaiting their human authority.
        """
        decisions = await auth_service.store.list_decisions(
            outcome=decision.value if decision else None,
            limit=max(1, min(limit, 500)),
        )

        if pending:
            decisions = [item for item in decisions if item.resolution is None]

        return decisions

    @get("/contexts")
    async def list_decision_contexts(
        self,
        auth_service: NamedDependency[AuthorizationService],
        decision: FromQuery[DecisionOutcome | None] = None,
        pending: FromQuery[bool] = False,
        limit: FromQuery[int] = 25,
    ) -> list[DecisionContext]:
        """Lists decisions together with the transaction and evidence behind each.

        A client that needs to show what a decision was *about* — which resource,
        which zone, what value — would otherwise call the per-decision context
        endpoint once per row. This returns the same information in one call.
        """
        store = auth_service.store
        decisions = await store.list_decisions(
            outcome=decision.value if decision else None,
            limit=max(1, min(limit, 100)),
        )

        if pending:
            decisions = [item for item in decisions if item.resolution is None]

        contexts: list[DecisionContext] = []
        for item in decisions:
            tx_id = item.transaction_id
            contexts.append(
                DecisionContext(
                    decision=item,
                    transaction=await store.get_transaction(tx_id),
                    evidence=await store.get_evidence(tx_id),
                    evidence_plan=await store.get_evidence_plan(tx_id),
                    receipt=await store.get_receipt_by_transaction_id(tx_id),
                )
            )

        return contexts

    @get("/{decision_id:str}")
    async def get_authorization_decision(
        self,
        decision_id: FromPath[str],
        auth_service: NamedDependency[AuthorizationService],
    ) -> PolicyDecision:
        """Retrieves a historical policy decision record by decision ID."""
        decision = await auth_service.store.get_decision(decision_id)
        if not decision:
            raise NotFoundException(detail=f"Authorization decision '{decision_id}' not found")
        return decision

    @get("/{decision_id:str}/context")
    async def get_decision_context(
        self,
        decision_id: FromPath[str],
        auth_service: NamedDependency[AuthorizationService],
    ) -> DecisionContext:
        """Returns the decision with the transaction and network evidence behind it.

        A HOLD hands authority to a person; they decide on the same evidence package
        the policy engine used, never on a fresh request for input.
        """
        decision = await auth_service.store.get_decision(decision_id)
        if not decision:
            raise NotFoundException(detail=f"Authorization decision '{decision_id}' not found")

        store = auth_service.store
        tx_id = decision.transaction_id

        return DecisionContext(
            decision=decision,
            transaction=await store.get_transaction(tx_id),
            evidence=await store.get_evidence(tx_id),
            evidence_plan=await store.get_evidence_plan(tx_id),
            receipt=await store.get_receipt_by_transaction_id(tx_id),
        )

    @post("/{decision_id:str}/resolve")
    async def resolve_hold(
        self,
        decision_id: FromPath[str],
        data: HoldResolutionRequest,
        auth_service: NamedDependency[AuthorizationService],
    ) -> AuthorizationResponse:
        """Records the final, binding human decision on a HOLD.

        Only a HOLD is eligible. A deterministic DENY cannot be overridden here.
        """
        try:
            decision, receipt = await auth_service.resolve_hold(
                decision_id=decision_id,
                outcome=data.outcome,
                resolved_by=data.resolved_by,
                note=data.note,
            )
        except DecisionNotFoundError as exc:
            raise NotFoundException(detail=str(exc)) from exc
        except HoldResolutionError as exc:
            raise ClientException(detail=str(exc), status_code=409) from exc

        return AuthorizationResponse(decision=decision, receipt=receipt)
