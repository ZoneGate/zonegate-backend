from litestar import Controller, get, post
from litestar.di import NamedDependency
from litestar.exceptions import NotFoundException
from litestar.params import FromPath
from zonegate.authorization.service import AuthorizationService
from zonegate.domain.actors import Actor, DeviceBinding


class ActorsController(Controller):
    path = "/v1/actors"

    @post()
    async def enroll_actor(
        self,
        data: Actor,
        auth_service: NamedDependency[AuthorizationService],
    ) -> Actor:
        """Enrolls or updates a human actor in the ZoneGate identity registry."""
        await auth_service.store.save_actor(data)
        return data

    @get("/{actor_id:str}")
    async def get_actor(
        self,
        actor_id: FromPath[str],
        auth_service: NamedDependency[AuthorizationService],
    ) -> Actor:
        """Retrieves an enrolled actor by actor ID."""
        actor = await auth_service.store.get_actor(actor_id)
        if not actor:
            raise NotFoundException(detail=f"Actor '{actor_id}' not found")
        return actor


class DeviceBindingsController(Controller):
    path = "/v1/device-bindings"

    @post()
    async def create_binding(
        self,
        data: DeviceBinding,
        auth_service: NamedDependency[AuthorizationService],
    ) -> DeviceBinding:
        """Registers or updates a verified hardware device binding for an actor."""
        await auth_service.store.save_device_binding(data)
        return data

    @get("/{actor_id:str}")
    async def get_binding(
        self,
        actor_id: FromPath[str],
        auth_service: NamedDependency[AuthorizationService],
    ) -> DeviceBinding:
        """Retrieves an active hardware device binding by actor ID."""
        binding = await auth_service.store.get_device_binding(actor_id)
        if not binding:
            raise NotFoundException(detail=f"Device binding for actor '{actor_id}' not found")
        return binding
