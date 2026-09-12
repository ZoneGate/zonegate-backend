"""Tests for the live Nokia Network as Code client.

The payloads below are the ones the production gateway actually returned when
each tool was called against it, not shapes copied out of the CAMARA
specifications. That distinction is the whole point of these tests: the REST
client this replaces was written from the specifications, agreed perfectly
with a mock written the same way, and could not have reached the real carrier.

What is pinned here: the tool names and argument shapes the gateway accepts,
that its real response bodies parse, that a 200 carrying a CAMARA error body
is not read as evidence, and that a capability this subscription does not
have ends up recorded as not collected rather than as a pass.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest

from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.evidence import EvidenceKind, ValidatedEvidencePlan
from zonegate.domain.transactions import TransactionRequest
from zonegate.evidence.gateway import EvidenceGateway
from zonegate.integrations.nokia.live import (
    CarrierCapabilityUnavailableError,
    NokiaLiveError,
    NokiaLiveEvidenceClient,
)
from zonegate.integrations.nokia.models import CamaraReachabilityResponse

# Verbatim from the live gateway.
LIVE_SIM_SWAP = '{"swapped":false}'
LIVE_DEVICE_SWAP = '{"swapped":false}'
LIVE_LOCATION = '{"verificationResult":"TRUE","lastLocationTime":"2026-09-12T16:22:43.925648"}'
LIVE_REACHABILITY = (
    '{"device":{"phoneNumber":"+99999991001"},"reachable":true,'
    '"connectivity":["DATA"],"lastStatusTime":"2026-09-12T16:22:43.581051Z"}'
)
LIVE_MISSING_IDENTIFIER = '{"detail":{"code":"MISSING_IDENTIFIER","message":"The device cannot be identified."}}'


def gateway_client(text: str, *, status: int = 200) -> tuple[NokiaLiveEvidenceClient, list[dict]]:
    """A live client wired to a transport that replays one canned tool result."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            status,
            json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}},
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        transport=transport,
        headers={"x-api-host": "network-as-code.nokia.rapidapi.com", "x-api-key": "k"},
    )
    return NokiaLiveEvidenceClient(api_key="k", client=http), sent


class TestTheCallsTheGatewayAccepts:
    @pytest.mark.asyncio
    async def test_sim_swap_is_asked_by_tool_name_with_phone_and_max_age(self):
        client, sent = gateway_client(LIVE_SIM_SWAP)

        result = await client.check_sim_swap("+99999991001", max_age_hours=240)

        assert result.swapped is False
        assert sent[0]["method"] == "tools/call"
        assert sent[0]["params"]["name"] == "checkSimSwap"
        assert sent[0]["params"]["arguments"] == {
            "phoneNumber": "+99999991001",
            "maxAge": 240,
        }

    @pytest.mark.asyncio
    async def test_device_swap_uses_its_own_tool(self):
        client, sent = gateway_client(LIVE_DEVICE_SWAP)

        result = await client.check_device_swap("+99999991001")

        assert result.swapped is False
        assert sent[0]["params"]["name"] == "checkDeviceSwap"

    @pytest.mark.asyncio
    async def test_the_area_type_is_upper_case_because_the_live_enum_is(self):
        # The REST client sent "Circle". The live schema's enum has one member
        # and it is "CIRCLE", so the old payload was rejected outright.
        client, sent = gateway_client(LIVE_LOCATION)

        await client.verify_location("+99999991001", 37.7749, -122.4194, 500)

        area = sent[0]["params"]["arguments"]["area"]
        assert area["areaType"] == "CIRCLE"
        assert area["center"] == {"latitude": 37.7749, "longitude": -122.4194}
        assert area["radius"] == 500

    @pytest.mark.asyncio
    async def test_reachability_wraps_the_number_in_a_device_object(self):
        client, sent = gateway_client(LIVE_REACHABILITY)

        await client.get_reachability("+99999991001")

        assert sent[0]["params"]["name"] == "getReachabilityStatus"
        assert sent[0]["params"]["arguments"] == {"device": {"phoneNumber": "+99999991001"}}


class TestTheAnswersTheGatewayGives:
    @pytest.mark.asyncio
    async def test_the_real_location_body_parses_despite_its_extra_field(self):
        # lastLocationTime is not in the model. Under extra="forbid" a correct
        # answer became a validation error and then a failed request.
        client, _ = gateway_client(LIVE_LOCATION)

        result = await client.verify_location("+99999991001", 37.7749, -122.4194, 500)

        assert result.verificationResult == "TRUE"
        assert result.lastLocationTime == "2026-09-12T16:22:43.925648"

    @pytest.mark.asyncio
    async def test_the_real_reachability_body_parses_and_reads_as_reachable(self):
        # The live gateway answers `reachable`, not `reachabilityStatus`.
        client, _ = gateway_client(LIVE_REACHABILITY)

        result = await client.get_reachability("+99999991001")

        assert result.is_reachable is True
        assert result.connectivity == ["DATA"]

    def test_the_rest_shape_still_reads_the_same_way(self):
        # Both surfaces have to land on one answer, or the mock and the live
        # carrier would disagree about a device that is equally reachable.
        assert CamaraReachabilityResponse(reachabilityStatus="CONNECTED_DATA").is_reachable is True
        assert CamaraReachabilityResponse(reachabilityStatus="NOT_CONNECTED").is_reachable is False

    def test_saying_nothing_about_reachability_is_not_saying_reachable(self):
        assert CamaraReachabilityResponse().is_reachable is None


class TestARefusalIsNotEvidence:
    @pytest.mark.asyncio
    async def test_a_camara_error_body_is_not_read_as_a_result(self):
        # The gateway answers 200 with a `detail` body. Parsing that as
        # evidence is how a refusal turns into a silent pass.
        client, _ = gateway_client(LIVE_MISSING_IDENTIFIER)

        with pytest.raises(CarrierCapabilityUnavailableError) as caught:
            await client.check_sim_swap("+99999991001")

        assert "MISSING_IDENTIFIER" in str(caught.value)

    @pytest.mark.asyncio
    async def test_number_verification_declines_rather_than_answering_false(self):
        # False would read as "the carrier says this is the wrong number".
        # What actually happened is that a server cannot obtain the device
        # token CAMARA identifies the subscriber with.
        client, sent = gateway_client(LIVE_SIM_SWAP)

        with pytest.raises(CarrierCapabilityUnavailableError):
            await client.verify_number("+99999991001")

        assert sent == []

    @pytest.mark.asyncio
    async def test_a_transport_failure_is_a_different_error_than_a_refusal(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="bad gateway")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = NokiaLiveEvidenceClient(api_key="k", client=http)

        with pytest.raises(NokiaLiveError):
            await client.check_sim_swap("+99999991001")

    @pytest.mark.asyncio
    async def test_a_json_rpc_error_is_raised_not_swallowed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "no such tool"}},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = NokiaLiveEvidenceClient(api_key="k", client=http)

        with pytest.raises(NokiaLiveError):
            await client.check_sim_swap("+99999991001")


class TestWhatTheGatewayDoesWithARefusal:
    @pytest.mark.asyncio
    async def test_a_declined_check_is_recorded_as_not_collected(self):
        """The pipeline must survive a capability the subscription lacks.

        Not collected is the honest record, and the policy engine already
        refuses to read it as a pass -- so the request is denied for a stated
        reason instead of crashing or quietly approving.
        """

        class DecliningClient:
            async def verify_number(self, phone_number):
                raise CarrierCapabilityUnavailableError("MISSING_IDENTIFIER")

            async def verify_location(self, phone_number, latitude, longitude, radius_meters):
                raise AssertionError("not planned")

            async def check_sim_swap(self, phone_number, max_age_hours=240):
                raise AssertionError("not planned")

            async def check_device_swap(self, phone_number, max_age_hours=240):
                raise AssertionError("not planned")

            async def get_reachability(self, phone_number):
                raise AssertionError("not planned")

        gateway = EvidenceGateway(nokia_client=DecliningClient())

        evidence = await gateway.collect_evidence(
            actor=Actor(
                actor_id="usr_cargo_operator_01",
                role="ROLE_CARGO_OPERATOR",
                permissions=["cargo:release"],
                registered_phone_number="+99999991001",
                registered_device_id="dev_imei_99887766",
                enrollment_status="ACTIVE",
            ),
            binding=DeviceBinding(
                actor_id="usr_cargo_operator_01",
                phone_number="+99999991001",
                device_id="dev_imei_99887766",
                bound_at=datetime.now(timezone.utc),
                is_active=True,
            ),
            transaction=TransactionRequest(
                transaction_id="tx_declined",
                actor_id="usr_cargo_operator_01",
                action="RELEASE_CARGO",
                resource_id="CT-700",
                zone="PORT_GATE_17",
                timestamp=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc),
            ),
            plan=ValidatedEvidencePlan(
                mandatory=[EvidenceKind.NUMBER_VERIFICATION],
                optional=[],
                combined=[EvidenceKind.NUMBER_VERIFICATION],
                rationale=["only the mandatory check"],
            ),
        )

        assert evidence.number_verified is None
