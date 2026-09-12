from litestar import Controller, Request, Response, get, post
from litestar.datastructures import Cookie
from litestar.di import NamedDependency
from litestar.exceptions import ClientException, NotAuthorizedException
from pydantic import BaseModel, ConfigDict, Field

from zonegate.authorization.console import (
    SESSION_COOKIE,
    ConsoleAuthService,
    InvalidCredentialsError,
    WeakPasswordError,
)
from zonegate.config import Settings
from zonegate.domain.actors import Actor


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=200)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(..., min_length=1, max_length=200)


class SessionResponse(BaseModel):
    """Who the console is signed in as. Never carries the credential itself."""

    model_config = ConfigDict(extra="forbid")

    actor: Actor


def _session_cookie(value: str, settings: Settings, max_age: int | None = None) -> Cookie:
    """The session cookie.

    httpOnly so no page script can read it, SameSite=Lax so it does not ride
    along on a cross-site request, and Secure whenever the deployment is served
    over TLS — over plain http on a demo machine a Secure cookie is simply
    never sent back.
    """
    return Cookie(
        key=SESSION_COOKIE,
        value=value,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        max_age=max_age,
    )


class AuthController(Controller):
    """Console sign-in.

    Nothing here creates an identity. An operator signs in against an actor
    already on the enrolled roster, which is why there is no registration
    endpoint and never will be one.
    """

    path = "/v1/auth"

    @post("/login", status_code=200)
    async def login(
        self,
        data: LoginRequest,
        console_auth: NamedDependency[ConsoleAuthService],
        settings: NamedDependency[Settings],
    ) -> Response[SessionResponse]:
        try:
            actor, session = await console_auth.sign_in(data.actor_id.strip(), data.password)
        except InvalidCredentialsError as exc:
            raise NotAuthorizedException(detail=str(exc)) from exc

        max_age = int(console_auth.session_ttl.total_seconds())
        return Response(
            SessionResponse(actor=actor),
            cookies=[_session_cookie(session.session_id, settings, max_age=max_age)],
        )

    @get("/session")
    async def read_session(
        self,
        request: Request,
        console_auth: NamedDependency[ConsoleAuthService],
    ) -> SessionResponse:
        """The signed-in operator, or 401 when nobody is."""
        actor = await console_auth.resolve(request.cookies.get(SESSION_COOKIE))
        if actor is None:
            raise NotAuthorizedException(detail="No console session")
        return SessionResponse(actor=actor)

    @post("/logout", status_code=204)
    async def logout(
        self,
        request: Request,
        console_auth: NamedDependency[ConsoleAuthService],
        settings: NamedDependency[Settings],
    ) -> Response[None]:
        await console_auth.sign_out(request.cookies.get(SESSION_COOKIE))
        # An empty cookie with max_age 0 is what actually clears it in the
        # browser; deleting the record alone leaves a dead cookie behind.
        return Response(
            None,
            status_code=204,
            cookies=[_session_cookie("", settings, max_age=0)],
        )

    @post("/password", status_code=204)
    async def change_password(
        self,
        data: PasswordChangeRequest,
        request: Request,
        console_auth: NamedDependency[ConsoleAuthService],
    ) -> None:
        """Changes the signed-in operator's own password.

        Scoped to the caller's own session on purpose: resetting somebody
        else's password is an enrollment action, not a self-service one.
        """
        actor = await console_auth.resolve(request.cookies.get(SESSION_COOKIE))
        if actor is None:
            raise NotAuthorizedException(detail="No console session")

        try:
            await console_auth.set_password(actor.actor_id, data.password)
        except WeakPasswordError as exc:
            raise ClientException(detail=str(exc)) from exc
