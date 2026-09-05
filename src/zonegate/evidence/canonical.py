from datetime import datetime, timezone
from zonegate.domain.evidence import CanonicalEvidence


def build_canonical_evidence(
    number_verified: bool | None = None,
    location_verified: bool | None = None,
    recent_sim_swap: bool | None = None,
    recent_device_swap: bool | None = None,
    reachable: bool | None = None,
    kyc_match: bool | None = None,
    collected_at: datetime | None = None,
) -> CanonicalEvidence:
    """Constructs a normalized CanonicalEvidence instance.

    Fields for evidence not requested or not collected remain None.
    """
    return CanonicalEvidence(
        number_verified=number_verified,
        location_verified=location_verified,
        recent_sim_swap=recent_sim_swap,
        recent_device_swap=recent_device_swap,
        reachable=reachable,
        kyc_match=kyc_match,
        collected_at=collected_at or datetime.now(timezone.utc),
    )
