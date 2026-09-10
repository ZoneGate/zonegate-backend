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
    claimed: bool = Field(default=False, description="Whether token has been single-use claimed")
    claimed_at: datetime | None = Field(default=None, description="Timestamp of claim if claimed")


class TokenService:
    """Manages creation, verification, and single-use claim of scoped authorization tokens."""

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
            claimed=False,
            claimed_at=None,
        )

    def verify_token(
        self,
        token: ScopedAuthorizationToken,
        expected_action: str | None = None,
        expected_resource_id: str | None = None,
        expected_zone: str | None = None,
        expected_actor_id: str | None = None,
    ) -> tuple[bool, str, str]:
        """Verifies integrity, expiration, replay (claim status), and scope match.

        Returns: (is_valid: bool, status: str, message: str)
        status values:
            "VALID", "REJECTED_INVALID_SIGNATURE", "REJECTED_EXPIRED",
            "REJECTED_ALREADY_CLAIMED", "REJECTED_SCOPE_MISMATCH"
        """
        now = datetime.now(timezone.utc)

        # 1. Signature check
        payload = {
            "token_id": token.token_id,
            "actor_id": token.actor_id,
            "action": token.action,
            "resource_id": token.resource_id,
            "zone": token.zone,
            "decision_id": token.decision_id,
            "issued_at": token.issued_at.isoformat(),
            "expires_at": token.expires_at.isoformat(),
        }
        raw_msg = json.dumps(payload, sort_keys=True).encode("utf-8")
        expected_sig = hmac.new(self._secret, raw_msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(token.signature, expected_sig):
            return False, "REJECTED_INVALID_SIGNATURE", "Cryptographic signature verification failed"

        # 2. Expiration check
        if now > token.expires_at:
            return False, "REJECTED_EXPIRED", f"Token expired at {token.expires_at.isoformat()}"

        # 3. Replay check (single-use claim)
        if token.claimed:
            return False, "REJECTED_ALREADY_CLAIMED", f"Token already claimed at {token.claimed_at.isoformat() if token.claimed_at else 'unknown'}"

        # 4. Scope mismatch checks
        if expected_action and token.action != expected_action:
            return False, "REJECTED_SCOPE_MISMATCH", f"Scope mismatch: token action '{token.action}' != requested action '{expected_action}'"
        if expected_resource_id and token.resource_id != expected_resource_id:
            return False, "REJECTED_SCOPE_MISMATCH", f"Scope mismatch: token resource '{token.resource_id}' != requested resource '{expected_resource_id}'"
        if expected_zone and token.zone != expected_zone:
            return False, "REJECTED_SCOPE_MISMATCH", f"Scope mismatch: token zone '{token.zone}' != requested zone '{expected_zone}'"
        if expected_actor_id and token.actor_id != expected_actor_id:
            return False, "REJECTED_SCOPE_MISMATCH", f"Scope mismatch: token actor '{token.actor_id}' != requested actor '{expected_actor_id}'"

        return True, "VALID", "Token is valid and eligible for claim"

    def claim_token(
        self,
        token: ScopedAuthorizationToken,
        expected_action: str | None = None,
        expected_resource_id: str | None = None,
        expected_zone: str | None = None,
        expected_actor_id: str | None = None,
    ) -> tuple[bool, str, str, ScopedAuthorizationToken]:
        """Validates and mutates token to claimed status.

        Returns: (claimed: bool, status: str, message: str, updated_token: ScopedAuthorizationToken)
        """
        is_valid, status, message = self.verify_token(
            token=token,
            expected_action=expected_action,
            expected_resource_id=expected_resource_id,
            expected_zone=expected_zone,
            expected_actor_id=expected_actor_id,
        )
        if not is_valid:
            return False, status, message, token

        claimed_token = token.model_copy(update={
            "claimed": True,
            "claimed_at": datetime.now(timezone.utc),
        })
        return True, "CLAIMED", "Token successfully claimed for physical execution", claimed_token
