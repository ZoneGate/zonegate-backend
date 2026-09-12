"""Who works where.

ZoneGate has two surfaces with two audiences. Cargo personnel request releases
from the mobile app in the field; the people a release is escalated to --
supervisors and officers -- decide on the web console. The two never share a
screen: an operator cannot sign in to the console, and a hold can only be
settled by the role the policy engine handed it to.

Roles are compared without their `ROLE_` prefix, because enrolment has used
both spellings (`CARGO_OPERATOR` and `ROLE_CARGO_OPERATOR`) for the same job.
"""

# The roles that work in the field. Everyone else on the roster is an
# authority and belongs on the console.
FIELD_ROLES: frozenset[str] = frozenset({"CARGO_OPERATOR"})


def normalize_role(role: str | None) -> str:
    """`role_cargo_operator ` and `CARGO_OPERATOR` are the same job."""
    value = (role or "").strip().upper()
    return value[len("ROLE_"):] if value.startswith("ROLE_") else value


def is_field_role(role: str | None) -> bool:
    """True for cargo personnel, who use the mobile app and never the console."""
    return normalize_role(role) in FIELD_ROLES


def holds_authority(role: str | None, required_authority: str | None) -> bool:
    """Whether someone in `role` may settle a hold that requires `required_authority`.

    A hold that names no authority cannot be settled by anyone: guessing who it
    was meant for is exactly the decision the engine declined to make.
    """
    if not required_authority:
        return False
    return normalize_role(role) == normalize_role(required_authority)
