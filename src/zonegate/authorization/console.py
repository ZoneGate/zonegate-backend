"""Console sign-in.

Separate from the authorization pipeline on purpose. A password says who is at
the keyboard of the operations console; it is never evidence, and it never
reaches the policy engine. The two only meet at the actor id, so an operator
can sign in solely against an identity already on the enrolled roster.
"""

from datetime import datetime, timedelta, timezone
import logging
import secrets

from zonegate.authorization.passwords import hash_password, verify_password
from zonegate.domain.actors import Actor
from zonegate.domain.credentials import ConsoleSession, OperatorCredential
from zonegate.storage.zova import ZoneGateStore

logger = logging.getLogger(__name__)

SESSION_COOKIE = "zonegate_session"
MIN_PASSWORD_LENGTH = 8


class ConsoleAuthError(Exception):
    """Base class for sign-in failures."""


class InvalidCredentialsError(ConsoleAuthError):
    """Wrong actor id, wrong password, or an actor with no console access."""


class WeakPasswordError(ConsoleAuthError):
    """The proposed password does not meet the minimum length."""


class ConsoleAuthService:
    def __init__(self, store: ZoneGateStore, session_ttl_hours: int = 12) -> None:
        self.store = store
        self.session_ttl = timedelta(hours=session_ttl_hours)

    async def set_password(self, actor_id: str, password: str) -> None:
        """Sets or replaces an actor's console password."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError(
                f"A console password must be at least {MIN_PASSWORD_LENGTH} characters"
            )

        await self.store.save_credential(
            OperatorCredential(
                actor_id=actor_id,
                password_hash=hash_password(password),
                updated_at=datetime.now(timezone.utc),
            )
        )

    async def sign_in(self, actor_id: str, password: str) -> tuple[Actor, ConsoleSession]:
        """Verifies a password and opens a session.

        Every failure returns the same message. Distinguishing "no such actor"
        from "wrong password" would turn the sign-in form into a way to
        enumerate the enrolled roster.
        """
        generic = InvalidCredentialsError("Employee ID or password is incorrect")

        actor = await self.store.get_actor(actor_id)
        credential = await self.store.get_credential(actor_id)

        if actor is None or credential is None:
            # Still spend the hashing time, so a missing actor is not
            # distinguishable from a wrong password by how long it took.
            verify_password(password, "pbkdf2_sha256$240000$00$00")
            raise generic

        if not verify_password(password, credential.password_hash):
            logger.warning("Console sign-in rejected for actor '%s'", actor_id)
            raise generic

        if actor.enrollment_status != "ACTIVE":
            logger.warning(
                "Console sign-in refused: actor '%s' is '%s'", actor_id, actor.enrollment_status
            )
            raise InvalidCredentialsError(
                f"Actor '{actor_id}' is '{actor.enrollment_status}' and cannot sign in"
            )

        now = datetime.now(timezone.utc)
        session = ConsoleSession(
            session_id=secrets.token_urlsafe(32),
            actor_id=actor_id,
            created_at=now,
            expires_at=now + self.session_ttl,
        )
        await self.store.save_console_session(session)
        logger.info("Console session opened for actor '%s'", actor_id)
        return actor, session

    async def resolve(self, session_id: str | None) -> Actor | None:
        """The actor a session cookie stands for, or None when it stands for nobody.

        An expired session is deleted as it is read, so a stale cookie cannot be
        replayed even if the record survives in storage.
        """
        if not session_id:
            return None

        session = await self.store.get_console_session(session_id)
        if session is None:
            return None

        if session.expires_at <= datetime.now(timezone.utc):
            await self.store.delete_console_session(session_id)
            return None

        return await self.store.get_actor(session.actor_id)

    async def sign_out(self, session_id: str | None) -> None:
        if session_id:
            await self.store.delete_console_session(session_id)
