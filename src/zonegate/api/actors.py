from datetime import datetime, timezone
from litestar import Controller, Request, get, post, put
from litestar.di import NamedDependency
from litestar.exceptions import ClientException, NotFoundException
from litestar.params import FromPath, FromQuery
from pydantic import BaseModel, ConfigDict, Field
from zonegate.api.guards import require_console_actor
from zonegate.authorization.console import ConsoleAuthService, WeakPasswordError
from zonegate.authorization.service import AuthorizationService
from zonegate.domain.actors import Actor, DeviceBinding


class EnrollmentRequest(BaseModel):
    """Registers an actor together with the device binding the gateway checks against."""
    model_config = ConfigDict(extra="forbid")

    actor: Actor
    device_id: str | None = Field(
        default=None,
        description="Device to bind; defaults to the actor's registered device",
    )
    password: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Optional initial console password. Omitted, the actor is enrolled for the "
            "authorization pipeline but cannot sign in to the console — which is the "
            "right shape for a field operator who only ever uses the mobile app."
        ),
    )


class EnrollmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: Actor
    binding: DeviceBinding


class PermissionUpdateRequest(BaseModel):
    """Replaces an actor's permission set.

    `expected_permissions` is what the editor was showing when the operator
    pressed save. If the stored set has moved on since, the write is refused
    rather than silently discarding whatever the other person changed.
    """

    model_config = ConfigDict(extra="forbid")

    permissions: list[str] = Field(..., max_length=200)
    expected_permissions: list[str] = Field(...)


class RosterEntry(BaseModel):
    """An enrolled actor with the state of their device binding."""

    model_config = ConfigDict(extra="forbid")

    actor: Actor
    binding: DeviceBinding | None = Field(
        default=None,
        description="None when the actor was enrolled without an active binding",
    )


class ActorsController(Controller):
    path = "/v1/actors"

    @get()
    async def list_actors(
        self,
        auth_service: NamedDependency[AuthorizationService],
        limit: FromQuery[int] = 200,
    ) -> list[RosterEntry]:
        """Returns the enrolled roster, most recently enrolled first.

        Each entry carries its binding so a console can show at a glance who can
        actually pass authorization: an actor whose binding is missing or
        inactive will be denied no matter what permissions they hold.
        """
        actors = await auth_service.store.list_actors(limit=max(1, min(limit, 500)))

        return [
            RosterEntry(
                actor=actor,
                binding=await auth_service.store.get_device_binding(actor.actor_id),
            )
            for actor in actors
        ]

    @post()
    async def enroll_actor(
        self,
        data: EnrollmentRequest,
        request: Request,
        auth_service: NamedDependency[AuthorizationService],
        console_auth: NamedDependency[ConsoleAuthService],
    ) -> EnrollmentResponse:
        """Enrolls an actor and binds their device.

        Without this the authorization pipeline can only ever answer DENY, because
        every request is checked against an enrolled actor and an active binding.
        Enrolment is a console action: only a signed-in authority may add people.
        """
        await require_console_actor(request, console_auth)
        binding = DeviceBinding(
            actor_id=data.actor.actor_id,
            phone_number=data.actor.registered_phone_number,
            device_id=data.device_id or data.actor.registered_device_id,
            bound_at=datetime.now(timezone.utc),
            is_active=True,
        )

        await auth_service.store.save_actor(data.actor)
        await auth_service.store.save_device_binding(binding)

        if data.password:
            try:
                await console_auth.set_password(data.actor.actor_id, data.password)
            except WeakPasswordError as exc:
                raise ClientException(detail=str(exc)) from exc

        return EnrollmentResponse(actor=data.actor, binding=binding)

    @put("/{actor_id:str}/permissions")
    async def update_permissions(
        self,
        actor_id: FromPath[str],
        data: PermissionUpdateRequest,
        request: Request,
        auth_service: NamedDependency[AuthorizationService],
        console_auth: NamedDependency[ConsoleAuthService],
    ) -> Actor:
        """Replaces an enrolled actor's permissions.

        Permissions are what Rule 1 of the policy engine checks, so this is the
        one console screen that can turn a DENY into an APPROVE. It only ever
        affects requests evaluated from here on.
        """
        await require_console_actor(request, console_auth)
        actor = await auth_service.store.get_actor(actor_id)
        if not actor:
            raise NotFoundException(detail=f"Actor '{actor_id}' is not enrolled")

        if sorted(actor.permissions) != sorted(data.expected_permissions):
            raise ClientException(
                status_code=409,
                detail=(
                    f"Permissions for '{actor_id}' changed while you were editing. "
                    "Reload the employee and reapply your change."
                ),
            )

        deduplicated = sorted({permission.strip() for permission in data.permissions if permission.strip()})
        updated = actor.model_copy(update={"permissions": deduplicated})
        await auth_service.store.save_actor(updated)
        return updated

    @get("/{actor_id:str}")
    async def get_actor(
        self,
        actor_id: FromPath[str],
        auth_service: NamedDependency[AuthorizationService],
    ) -> EnrollmentResponse:
        """Retrieves an enrolled actor together with their active device binding."""
        actor = await auth_service.store.get_actor(actor_id)
        if not actor:
            raise NotFoundException(detail=f"Actor '{actor_id}' is not enrolled")

        binding = await auth_service.store.get_device_binding(actor_id)
        if not binding:
            raise NotFoundException(detail=f"Actor '{actor_id}' has no active device binding")

        return EnrollmentResponse(actor=actor, binding=binding)
