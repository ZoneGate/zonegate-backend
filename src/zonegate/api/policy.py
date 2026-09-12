from litestar import Controller, get, put
from litestar.di import NamedDependency
from pydantic import BaseModel, ConfigDict, Field
from zonegate.authorization.service import AuthorizationService
from zonegate.domain.policy_config import CARGO_CATEGORIES, PolicyConfig


class CategoryOption(BaseModel):
    """One cargo category, and who has to approve it if anyone does."""

    model_config = ConfigDict(extra="forbid")

    category: str
    restricted: bool = Field(..., description="True when a release of this category is escalated")
    required_authority: str | None = Field(
        default=None,
        description="Role the category is handed to; None when it releases automatically",
    )


class GeofenceZone(BaseModel):
    """A zone as the evidence gateway actually asks the carrier about it."""

    model_config = ConfigDict(extra="forbid")

    zone: str
    latitude: float
    longitude: float
    radius_meters: int


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

    @get("/categories")
    async def list_categories(
        self,
        auth_service: NamedDependency[AuthorizationService],
    ) -> list[CategoryOption]:
        """The cargo categories a request can carry, with their current handling.

        Categories the config restricts but that are not in the built-in
        vocabulary are listed too, so a console never hides a rule that is
        actually in force.
        """
        config = auth_service.policy_engine.config
        names = list(CARGO_CATEGORIES) + [
            name for name in sorted(config.restricted_categories) if name not in CARGO_CATEGORIES
        ]

        return [
            CategoryOption(
                category=name,
                restricted=config.authority_for(name) is not None,
                required_authority=config.authority_for(name),
            )
            for name in names
        ]

    @get("/zones")
    async def list_zones(
        self,
        auth_service: NamedDependency[AuthorizationService],
    ) -> list[GeofenceZone]:
        """The geofences the evidence gateway verifies device location against."""
        return [
            GeofenceZone(zone=zone, latitude=lat, longitude=lon, radius_meters=radius)
            for zone, (lat, lon, radius) in sorted(auth_service.gateway.zone_registry.items())
        ]

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
