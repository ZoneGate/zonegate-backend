from datetime import datetime, timezone
from litestar import Controller, get, post
from litestar.di import NamedDependency
from litestar.exceptions import NotFoundException
from litestar.params import FromPath, FromQuery
from pydantic import BaseModel, ConfigDict, Field
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


class EnrollmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: Actor
    binding: DeviceBinding


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
        auth_service: NamedDependency[AuthorizationService],
    ) -> EnrollmentResponse:
        """Enrolls an actor and binds their device.

        Without this the authorization pipeline can only ever answer DENY, because
        every request is checked against an enrolled actor and an active binding.
        """
        binding = DeviceBinding(
            actor_id=data.actor.actor_id,
            phone_number=data.actor.registered_phone_number,
            device_id=data.device_id or data.actor.registered_device_id,
            bound_at=datetime.now(timezone.utc),
            is_active=True,
        )

        await auth_service.store.save_actor(data.actor)
        await auth_service.store.save_device_binding(binding)

        return EnrollmentResponse(actor=data.actor, binding=binding)

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
