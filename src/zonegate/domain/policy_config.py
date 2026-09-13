from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The cargo categories the console offers. A request carries exactly one.
#:
#: This is the vocabulary, not the policy: which of these are restricted, and
#: to which authority, is configuration — see `PolicyConfig`.
CARGO_CATEGORIES: tuple[str, ...] = (
    "GENERAL",
    "PERISHABLE",
    "HIGH_VALUE",
    "HAZARDOUS",
    "CONTROLLED_SUBSTANCE",
    "WEAPONS",
)

DEFAULT_CATEGORY = "GENERAL"

#: Fields an older configuration may still carry. The operational window was
#: removed -- a release is no longer held for the hour it is requested at -- but
#: a config saved while it existed stores both hours next to its category map.
RETIRED_FIELDS: frozenset[str] = frozenset({"window_start_hour", "window_end_hour"})

#: Categories that may not be released on the engine's own authority, and the
#: role each one is handed to. A category absent from this map releases
#: automatically once the network evidence passes.
DEFAULT_RESTRICTED_CATEGORIES: dict[str, str] = {
    "HIGH_VALUE": "ROLE_CARGO_SUPERVISOR",
    "HAZARDOUS": "ROLE_SAFETY_OFFICER",
    "CONTROLLED_SUBSTANCE": "ROLE_COMPLIANCE_OFFICER",
    "WEAPONS": "ROLE_SECURITY_OFFICER",
}


class PolicyConfig(BaseModel):
    """The knobs an operations lead is allowed to turn.

    Everything else about a decision — which rules exist, their order, and the
    fact that the agent's assessment is advisory — is fixed in code and
    deliberately not configurable from an API.

    Restricted categories replaced a single monetary threshold: what makes a
    release sensitive is what is in the container, not only what it is worth,
    and each kind of sensitivity answers to a different role.
    """

    model_config = ConfigDict(extra="forbid")

    restricted_categories: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_RESTRICTED_CATEGORIES),
        description="Cargo category -> the authority role that must approve it",
    )
    @model_validator(mode="before")
    @classmethod
    def _drop_retired_fields(cls, data: object) -> object:
        # Refusing a config for carrying the old window would throw away the
        # category map saved alongside it, and a console built before the
        # change would be unable to save at all.
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if key not in RETIRED_FIELDS}
        return data

    @field_validator("restricted_categories")
    @classmethod
    def _check_categories(cls, value: dict[str, str]) -> dict[str, str]:
        for category, authority in value.items():
            if not category.strip():
                raise ValueError("A restricted category name cannot be blank")
            if not authority.strip():
                raise ValueError(
                    f"Category '{category}' is restricted but names no approving authority. "
                    "A category with nobody to escalate to would silently release."
                )
        return {category.strip(): authority.strip() for category, authority in value.items()}

    def authority_for(self, category: str) -> str | None:
        """The role a category must be escalated to, or None if it is unrestricted."""
        return self.restricted_categories.get(category)
