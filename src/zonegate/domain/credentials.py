"""Console sign-in records.

These exist only so a person can open the operations console. They are separate
from the network-attested identity the authorization pipeline works with: a
password proves who is at the keyboard, it never contributes evidence to a
decision.
"""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class OperatorCredential(BaseModel):
    """The console password of an already-enrolled actor, stored hashed."""

    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., description="Actor this credential signs in as")
    password_hash: str = Field(..., description="PBKDF2-SHA256 encoded hash; never the password")
    updated_at: datetime = Field(..., description="UTC timestamp the password was last set")


class ConsoleSession(BaseModel):
    """A signed-in console session, keyed by an opaque cookie value."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="Opaque random identifier held in the session cookie")
    actor_id: str = Field(..., description="Actor the session is signed in as")
    created_at: datetime = Field(..., description="UTC timestamp the session was opened")
    expires_at: datetime = Field(..., description="UTC timestamp after which the session is refused")
