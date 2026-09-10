from litestar import Controller, get, put
from litestar.di import NamedDependency
from zonegate.authorization.service import AuthorizationService
from zonegate.domain.policy_config import PolicyConfig


class PolicyController(Controller):
    """Reads and updates the thresholds the deterministic engine evaluates against.

    Only thresholds are exposed. The rules themselves, their order, and the fact
    that the agent's assessment can never override them are fixed in code — a
    console can retune a limit, it cannot change how a decision is reached.
    """

    path = "/v1/policy"

    @get("/config")
    async def get_config(
        self,
        auth_service: NamedDependency[AuthorizationService],
    ) -> PolicyConfig:
        """Returns the configuration the engine is currently evaluating with."""
        return auth_service.policy_engine.config

    @put("/config")
    async def update_config(
        self,
        data: PolicyConfig,
        auth_service: NamedDependency[AuthorizationService],
    ) -> PolicyConfig:
        """Persists new thresholds and applies them to the running engine.

        Decisions already recorded keep the reasons they were given; this only
        affects transactions evaluated from here on.
        """
        await auth_service.store.save_policy_config(data)
        auth_service.policy_engine.config = data
        return data
