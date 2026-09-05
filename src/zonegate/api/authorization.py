from litestar import Controller, get, post
from litestar.di import NamedDependency
from litestar.exceptions import NotFoundException
from litestar.params import FromPath
from pydantic import BaseModel, ConfigDict
from zonegate.authorization.service import AuthorizationService
from zonegate.domain.decisions import PolicyDecision
from zonegate.domain.receipts import Receipt
from zonegate.domain.transactions import TransactionRequest


class AuthorizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision
    receipt: Receipt


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
