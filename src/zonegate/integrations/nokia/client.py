from typing import Protocol
import httpx
from zonegate.domain.evidence import EvidenceKind
from zonegate.integrations.nokia.models import (
    CamaraCircleArea,
    CamaraCoordinates,
    CamaraDeviceRef,
    CamaraDeviceSwapRequest,
    CamaraDeviceSwapResponse,
    CamaraLocationVerificationRequest,
    CamaraLocationVerificationResponse,
    CamaraNumberVerificationRequest,
    CamaraNumberVerificationResponse,
    CamaraReachabilityRequest,
    CamaraReachabilityResponse,
    CamaraSimSwapRequest,
    CamaraSimSwapResponse,
)


class NokiaClientProtocol(Protocol):
    # What this carrier can answer at all. A client that does not declare it
    # is taken to answer everything, which is the strictest reading.
    attestable_kinds: frozenset[EvidenceKind]

    async def verify_number(self, phone_number: str) -> CamaraNumberVerificationResponse:
        ...

    async def verify_location(
        self,
        phone_number: str,
        latitude: float,
        longitude: float,
        radius_meters: int,
    ) -> CamaraLocationVerificationResponse:
        ...

    async def check_sim_swap(
        self,
        phone_number: str,
        max_age_hours: int = 240,
    ) -> CamaraSimSwapResponse:
        ...

    async def check_device_swap(
        self,
        phone_number: str,
        max_age_hours: int = 240,
    ) -> CamaraDeviceSwapResponse:
        ...

    async def get_reachability(self, phone_number: str) -> CamaraReachabilityResponse:
        ...


class NokiaEvidenceClient:
    """A CAMARA-shaped REST client, pointed at a base URL.

    This is what a CAMARA-conformant operator endpoint serves. It is **not**
    the Nokia Network as Code production gateway: that gateway exposes these
    capabilities as MCP tools at different paths and versions and
    authenticates with `x-api-key`, so these requests do not reach it. Use
    `NokiaLiveEvidenceClient` for Nokia and keep this one for carriers that
    expose CAMARA REST directly.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # A conformant CAMARA endpoint answers all five, so nothing here is
        # exempt from the rules that depend on them.
        self.attestable_kinds = frozenset(EvidenceKind)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=10.0,
        )

    async def verify_number(self, phone_number: str) -> CamaraNumberVerificationResponse:
        # CAMARA Number Verification endpoint
        # TODO: Wire to carrier partner specific path if routed via Nokia NaC aggregations
        payload = CamaraNumberVerificationRequest(phoneNumber=phone_number).model_dump()
        response = await self._client.post("/number-verification/v1/verify", json=payload)
        response.raise_for_status()
        return CamaraNumberVerificationResponse.model_validate(response.json())

    async def verify_location(
        self,
        phone_number: str,
        latitude: float,
        longitude: float,
        radius_meters: int,
    ) -> CamaraLocationVerificationResponse:
        # CAMARA Location Verification endpoint
        payload = CamaraLocationVerificationRequest(
            device=CamaraDeviceRef(phoneNumber=phone_number),
            area=CamaraCircleArea(
                areaType="Circle",
                center=CamaraCoordinates(latitude=latitude, longitude=longitude),
                radius=radius_meters,
            ),
        ).model_dump()
        response = await self._client.post("/location-verification/v1/verify", json=payload)
        response.raise_for_status()
        return CamaraLocationVerificationResponse.model_validate(response.json())

    async def check_sim_swap(
        self,
        phone_number: str,
        max_age_hours: int = 240,
    ) -> CamaraSimSwapResponse:
        # CAMARA SIM Swap check endpoint
        payload = CamaraSimSwapRequest(phoneNumber=phone_number, maxAge=max_age_hours).model_dump()
        response = await self._client.post("/sim-swap/v1/check", json=payload)
        response.raise_for_status()
        return CamaraSimSwapResponse.model_validate(response.json())

    async def check_device_swap(
        self,
        phone_number: str,
        max_age_hours: int = 240,
    ) -> CamaraDeviceSwapResponse:
        # CAMARA Device Swap (IMEI check) endpoint
        # TODO: Confirm target operator support for device-swap in destination telecom zone
        payload = CamaraDeviceSwapRequest(phoneNumber=phone_number, maxAge=max_age_hours).model_dump()
        response = await self._client.post("/device-swap/v1/check", json=payload)
        response.raise_for_status()
        return CamaraDeviceSwapResponse.model_validate(response.json())

    async def get_reachability(self, phone_number: str) -> CamaraReachabilityResponse:
        # CAMARA Device Reachability endpoint
        payload = CamaraReachabilityRequest(
            device=CamaraDeviceRef(phoneNumber=phone_number)
        ).model_dump()
        response = await self._client.post("/device-reachability/v1/reachability-status", json=payload)
        response.raise_for_status()
        return CamaraReachabilityResponse.model_validate(response.json())
