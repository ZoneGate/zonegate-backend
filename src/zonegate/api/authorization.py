from litestar import Controller, get, post
from litestar.di import NamedDependency
from litestar.exceptions import ClientException, NotFoundException
from litestar.params import FromPath, FromQuery
from pydantic import BaseModel, ConfigDict, Field
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


class AuthorizationController(Controller):
    path = "/v1/authorizations"

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
