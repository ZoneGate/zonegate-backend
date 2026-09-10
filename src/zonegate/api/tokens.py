from datetime import datetime
from litestar import Controller, get, post
from litestar.di import NamedDependency
from litestar.exceptions import NotFoundException
from litestar.params import FromPath
from pydantic import BaseModel, ConfigDict, Field
from zonegate.authorization.service import AuthorizationService
from zonegate.authorization.token import ScopedAuthorizationToken


class TokenClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_id: str = Field(..., description="Unique single-use token ID (or signature) to claim")
    action: str | None = Field(default=None, description="Expected operational action (e.g. RELEASE_CARGO)")
    resource_id: str | None = Field(default=None, description="Expected resource identifier (e.g. container/gate ID)")
    zone: str | None = Field(default=None, description="Operational zone where claim takes place")
    actor_id: str | None = Field(default=None, description="Actor presenting the token")


class TokenClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claimed: bool = Field(..., description="Whether the claim succeeded and action may proceed")
    status: str = Field(..., description="CLAIMED, REJECTED_ALREADY_CLAIMED, REJECTED_EXPIRED, REJECTED_SCOPE_MISMATCH, REJECTED_INVALID_SIGNATURE, REJECTED_NOT_FOUND")
    message: str = Field(..., description="Descriptive status message or rejection rationale")
    token_id: str = Field(..., description="Claimed token identifier")
    claimed_at: datetime | None = Field(default=None, description="Timestamp of successful claim")


class TokensController(Controller):
    path = "/v1/tokens"

    @post("/claim")
    async def claim_token(
        self,
        data: TokenClaimRequest,
        auth_service: NamedDependency[AuthorizationService],
    ) -> TokenClaimResponse:
        """Claims a single-use authorization token for physical execution with replay & scope verification."""
        claimed, status, message, updated_token = await auth_service.claim_token(
            token_identifier=data.token_id,
            expected_action=data.action,
            expected_resource_id=data.resource_id,
            expected_zone=data.zone,
            expected_actor_id=data.actor_id,
        )
        return TokenClaimResponse(
            claimed=claimed,
            status=status,
            message=message,
            token_id=data.token_id,
            claimed_at=updated_token.claimed_at if updated_token else None,
        )

    @get("/{token_id:str}")
    async def get_token(
        self,
        token_id: FromPath[str],
        auth_service: NamedDependency[AuthorizationService],
    ) -> ScopedAuthorizationToken:
        """Retrieves a stored authorization token record by token ID or signature."""
        token = await auth_service.store.get_token(token_id)
        if not token:
            raise NotFoundException(detail=f"Authorization token '{token_id}' not found")
        return token
