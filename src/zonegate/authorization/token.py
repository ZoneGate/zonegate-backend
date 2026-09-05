from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import uuid
from pydantic import BaseModel, ConfigDict, Field


class ScopedAuthorizationToken(BaseModel):
    """Cryptographically scoped token representing human authorization for a single protected action."""
    model_config = ConfigDict(extra="forbid")

    token_id: str = Field(..., description="Unique single-use identifier (jti)")
    actor_id: str = Field(..., description="Bound actor identifier")
    action: str = Field(..., description="Bound action identifier")
    resource_id: str = Field(..., description="Bound target resource identifier")
    zone: str = Field(..., description="Bound operational zone")
    decision_id: str = Field(..., description="Correlated PolicyDecision identifier")
    issued_at: datetime = Field(..., description="Timestamp of issuance")
    expires_at: datetime = Field(..., description="Token expiry timestamp")
    signature: str = Field(..., description="Integrity HMAC signature")


class TokenService:
    """Manages creation and verification of scoped single-use authorization tokens."""

    def __init__(self, secret_key: str = "zonegate-token-secret-key-initial-scaffold") -> None:
        self._secret = secret_key.encode("utf-8")

    def issue_token(
        self,
        actor_id: str,
        action: str,
        resource_id: str,
        zone: str,
        decision_id: str,
        ttl_seconds: int = 300,
    ) -> ScopedAuthorizationToken:
        now = datetime.now(timezone.utc)
        token_id = f"tok_{uuid.uuid4().hex}"
        expires_at = now + timedelta(seconds=ttl_seconds)

        payload = {
            "token_id": token_id,
            "actor_id": actor_id,
            "action": action,
            "resource_id": resource_id,
            "zone": zone,
            "decision_id": decision_id,
            "issued_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        raw_msg = json.dumps(payload, sort_keys=True).encode("utf-8")
        signature = hmac.new(self._secret, raw_msg, hashlib.sha256).hexdigest()

        return ScopedAuthorizationToken(
            token_id=token_id,
            actor_id=actor_id,
            action=action,
            resource_id=resource_id,
            zone=zone,
            decision_id=decision_id,
            issued_at=now,
            expires_at=expires_at,
            signature=signature,
        )
