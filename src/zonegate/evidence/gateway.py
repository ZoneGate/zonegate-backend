from datetime import datetime, timezone
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.evidence import CanonicalEvidence, EvidenceKind, ValidatedEvidencePlan
from zonegate.domain.transactions import TransactionRequest
from zonegate.integrations.nokia.client import NokiaClientProtocol


class EvidenceGatewayError(Exception):
    """Base exception for Evidence Gateway failures."""


class ActorDeviceMismatchError(EvidenceGatewayError):
    """Raised when actor identity does not match verified device binding."""


class UnauthorizedEvidenceRequestError(EvidenceGatewayError):
    """Raised when an unauthorized or unapproved evidence kind is attempted."""


# Static geofence zones: zone_name -> (latitude, longitude, radius_meters)
#
# A zone that is not here falls back to DEFAULT_ZONE, which means the carrier
# is asked about a different circle than the one named on the request. Any zone
# the clients actually send therefore has to be listed, or the location check
# quietly answers a question nobody asked.
DEFAULT_ZONE_REGISTRY: dict[str, tuple[float, float, int]] = {
    "ZONE_CARGO_BAY_1": (37.7749, -122.4194, 500),
    "ZONE_PORT_TERMINAL_A": (37.7899, -122.4014, 1000),
    "PORT_GATE_17": (37.7955, -122.3937, 400),
    "DEFAULT_ZONE": (37.7749, -122.4194, 1000),
}

# Static gateway capability allowlist
SUPPORTED_CAPABILITIES: frozenset[EvidenceKind] = frozenset({
    EvidenceKind.NUMBER_VERIFICATION,
    EvidenceKind.LOCATION_VERIFICATION,
    EvidenceKind.SIM_SWAP,
    EvidenceKind.DEVICE_SWAP,
    EvidenceKind.REACHABILITY,
})


class EvidenceGateway:
    """The sole gateway authorized to invoke Nokia / CAMARA network APIs.

    Enforces runtime actor-device binding, authorization against the validated plan,
    parameter verification, and canonical evidence normalization.
    """

    def __init__(
        self,
        nokia_client: NokiaClientProtocol,
        zone_registry: dict[str, tuple[float, float, int]] | None = None,
    ) -> None:
        self._nokia_client = nokia_client
        self._zone_registry = zone_registry or DEFAULT_ZONE_REGISTRY

    @property
    def zone_registry(self) -> dict[str, tuple[float, float, int]]:
        """The geofences this gateway checks against, keyed by zone name.

        Exposed so a console can draw the same circle the carrier was asked
        about, rather than an illustration of one.
        """
        return dict(self._zone_registry)

    def validate_actor_device_binding(
        self,
        actor: Actor,
        binding: DeviceBinding,
    ) -> None:
        """Verifies cryptographic and identity binding between human actor and hardware."""
        if actor.actor_id != binding.actor_id:
            raise ActorDeviceMismatchError(
                f"Actor ID '{actor.actor_id}' does not match binding actor ID '{binding.actor_id}'"
            )
        if actor.registered_phone_number != binding.phone_number:
            raise ActorDeviceMismatchError("Registered phone number does not match device binding")
        if actor.registered_device_id != binding.device_id:
            raise ActorDeviceMismatchError("Registered device ID does not match device binding")
        if not binding.is_active:
            raise ActorDeviceMismatchError(f"Device binding for actor '{actor.actor_id}' is inactive")
        if actor.enrollment_status != "ACTIVE":
            raise ActorDeviceMismatchError(
                f"Actor '{actor.actor_id}' status is '{actor.enrollment_status}', expected 'ACTIVE'"
            )

    async def collect_evidence(
        self,
        actor: Actor,
        binding: DeviceBinding,
        transaction: TransactionRequest,
        plan: ValidatedEvidencePlan,
    ) -> CanonicalEvidence:
        """Collects network evidence strictly according to the validated plan."""
        # 1. Enforce actor-device binding
        self.validate_actor_device_binding(actor, binding)

        # 2. Enforce runtime authorization against static capability allowlist
        for evidence_kind in plan.combined:
            if evidence_kind not in SUPPORTED_CAPABILITIES:
                raise UnauthorizedEvidenceRequestError(
                    f"Evidence kind '{evidence_kind}' is not supported or authorized by the gateway"
                )

        # Resolve geofence coordinates for the target zone
        lat, lon, radius = self._zone_registry.get(
            transaction.zone, self._zone_registry["DEFAULT_ZONE"]
        )

        # 3. Invoke Nokia adapter strictly for approved kinds
        number_verified: bool | None = None
        location_verified: bool | None = None
        recent_sim_swap: bool | None = None
        recent_device_swap: bool | None = None
        reachable: bool | None = None
        kyc_match: bool | None = None

        try:
            if EvidenceKind.NUMBER_VERIFICATION in plan.combined:
                res = await self._nokia_client.verify_number(binding.phone_number)
                number_verified = res.devicePhoneNumberVerified

            if EvidenceKind.LOCATION_VERIFICATION in plan.combined:
                res = await self._nokia_client.verify_location(
                    binding.phone_number, lat, lon, radius
                )
                location_verified = res.verificationResult == "TRUE"

            if EvidenceKind.SIM_SWAP in plan.combined:
                res = await self._nokia_client.check_sim_swap(binding.phone_number)
                recent_sim_swap = res.swapped

            if EvidenceKind.DEVICE_SWAP in plan.combined:
                res = await self._nokia_client.check_device_swap(binding.phone_number)
                recent_device_swap = res.swapped

            if EvidenceKind.REACHABILITY in plan.combined:
                res = await self._nokia_client.get_reachability(binding.phone_number)
                reachable = res.reachabilityStatus in ("CONNECTED_DATA", "CONNECTED_SMS")
        except EvidenceGatewayError:
            raise
        except Exception as exc:
            raise EvidenceGatewayError(f"Nokia carrier gateway communication error: {exc}") from exc

        # 4. Normalize to CanonicalEvidence
        return CanonicalEvidence(
            number_verified=number_verified,
            location_verified=location_verified,
            recent_sim_swap=recent_sim_swap,
            recent_device_swap=recent_device_swap,
            reachable=reachable,
            kyc_match=kyc_match,
            collected_at=datetime.now(timezone.utc),
        )
