"""The Nokia Network as Code gateway as it actually answers.

`NokiaEvidenceClient` speaks plain CAMARA REST against a base URL, which is
what the bundled mock serves. The production gateway is not that: the only
reachable surface for this subscription is the Network as Code MCP endpoint,
which takes JSON-RPC `tools/call` requests and identifies the caller with
`x-api-key` / `x-api-host` headers rather than a bearer token. The CAMARA
paths sit behind tool names, several of them at a different version than the
REST client assumed, so a request shaped for the mock never reaches the real
carrier at all.

Every tool name and payload below was read off `tools/list` on the live
endpoint and then exercised against it, so the shapes here are observed
rather than inferred from the CAMARA specifications.
"""

import json
import logging
from typing import Any

import httpx

from zonegate.integrations.nokia.models import (
    CamaraDeviceSwapResponse,
    CamaraLocationVerificationResponse,
    CamaraNumberVerificationResponse,
    CamaraReachabilityResponse,
    CamaraSimSwapResponse,
)

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "https://mcp.prodeu.apihub.nokia.io"
DEFAULT_API_HOST = "network-as-code.nokia.rapidapi.com"

# Tool names as the live gateway lists them. Kept here rather than inline so
# the static allowlist and the calls cannot drift apart.
TOOL_NUMBER_VERIFICATION = "phoneNumberVerify-NV-V2"
TOOL_LOCATION_VERIFICATION = "verifyLocation-LocV-V0"
TOOL_SIM_SWAP = "checkSimSwap"
TOOL_DEVICE_SWAP = "checkDeviceSwap"
TOOL_REACHABILITY = "getReachabilityStatus"


class NokiaLiveError(Exception):
    """The live gateway could not be reached, or answered unintelligibly."""


class CarrierCapabilityUnavailableError(Exception):
    """The carrier will not answer this check for this subscription.

    Separate from `NokiaLiveError` on purpose. A transport failure means the
    evidence is unknown because something broke, and the request should fail
    loudly. This means the carrier answered and declined, which is a fact
    about the evidence: it must end up recorded as not collected, never as a
    pass, and never as a failed check that reads as the operator's fault.
    """


class NokiaLiveEvidenceClient:
    """`NokiaClientProtocol` against the real Network as Code gateway.

    Note on number verification: CAMARA identifies the device from a
    three-legged token minted over the device's own mobile connection, not
    from the phone number in the body. Called from a server with no such
    token, the live gateway answers `MISSING_IDENTIFIER`. Reporting that as
    `False` would read as "the carrier says this is the wrong number", which
    is not what happened, so this client declines instead.
    """

    def __init__(
        self,
        api_key: str,
        *,
        mcp_url: str = DEFAULT_MCP_URL,
        api_host: str = DEFAULT_API_HOST,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.mcp_url = mcp_url.rstrip("/")
        self.api_host = api_host
        self.api_key = api_key
        self._request_id = 0
        self._client = client or httpx.AsyncClient(
            headers={
                "Content-Type": "application/json",
                # The endpoint negotiates either a JSON body or an event
                # stream; asking for both keeps it from rejecting the request.
                "Accept": "application/json, text/event-stream",
                "x-api-host": api_host,
                "x-api-key": api_key,
            },
            timeout=timeout,
        )

    async def _call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one carrier tool and return the CAMARA payload it carried."""
        self._request_id += 1
        envelope = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }

        try:
            response = await self._client.post(self.mcp_url, json=envelope)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise NokiaLiveError(f"Nokia gateway transport failure on '{tool}': {exc}") from exc
        except ValueError as exc:
            raise NokiaLiveError(f"Nokia gateway returned a non-JSON body for '{tool}'") from exc

        if "error" in body:
            raise NokiaLiveError(f"Nokia gateway refused '{tool}': {body['error']}")

        # The carrier payload arrives as JSON inside a text content block.
        try:
            payload = json.loads(body["result"]["content"][0]["text"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise NokiaLiveError(f"Nokia gateway answered '{tool}' in an unreadable shape") from exc

        if not isinstance(payload, dict):
            raise NokiaLiveError(f"Nokia gateway answered '{tool}' with {type(payload).__name__}")

        # A declined call comes back 200 carrying a CAMARA error body rather
        # than an HTTP error, so looking at the body is the only way to notice.
        if "detail" in payload or "status" in payload or "code" in payload:
            raise CarrierCapabilityUnavailableError(
                f"The carrier declined '{tool}': {_describe(payload)}"
            )

        return payload

    async def verify_number(self, phone_number: str) -> CamaraNumberVerificationResponse:
        raise CarrierCapabilityUnavailableError(
            "Number verification needs an access token minted over the device's own "
            "mobile connection; a server holding only the phone number cannot obtain "
            "one, and the carrier answers MISSING_IDENTIFIER"
        )

    async def verify_location(
        self,
        phone_number: str,
        latitude: float,
        longitude: float,
        radius_meters: int,
    ) -> CamaraLocationVerificationResponse:
        payload = await self._call_tool(
            TOOL_LOCATION_VERIFICATION,
            {
                "device": {"phoneNumber": phone_number},
                "area": {
                    # The live enum is upper case; "Circle" is rejected.
                    "areaType": "CIRCLE",
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": radius_meters,
                },
            },
        )
        return CamaraLocationVerificationResponse.model_validate(payload)

    async def check_sim_swap(
        self,
        phone_number: str,
        max_age_hours: int = 240,
    ) -> CamaraSimSwapResponse:
        payload = await self._call_tool(
            TOOL_SIM_SWAP,
            {"phoneNumber": phone_number, "maxAge": max_age_hours},
        )
        return CamaraSimSwapResponse.model_validate(payload)

    async def check_device_swap(
        self,
        phone_number: str,
        max_age_hours: int = 240,
    ) -> CamaraDeviceSwapResponse:
        payload = await self._call_tool(
            TOOL_DEVICE_SWAP,
            {"phoneNumber": phone_number, "maxAge": max_age_hours},
        )
        return CamaraDeviceSwapResponse.model_validate(payload)

    async def get_reachability(self, phone_number: str) -> CamaraReachabilityResponse:
        payload = await self._call_tool(
            TOOL_REACHABILITY,
            {"device": {"phoneNumber": phone_number}},
        )
        return CamaraReachabilityResponse.model_validate(payload)

    async def aclose(self) -> None:
        await self._client.aclose()


def _describe(payload: dict[str, Any]) -> str:
    """The carrier's own words for why it declined, without inventing any."""
    detail = payload.get("detail")
    if isinstance(detail, dict):
        code = detail.get("code", "")
        message = detail.get("message", "")
        return f"{code} {message}".strip() or json.dumps(detail)
    if isinstance(detail, str):
        return detail
    return json.dumps(payload)
