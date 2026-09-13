from datetime import datetime, timezone
from decimal import Decimal
import pytest
from litestar.testing import AsyncTestClient
from zonegate.agent.ollama import OllamaClient
from zonegate.app import create_app
from zonegate.config import Settings
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.evidence import CanonicalEvidence
from zonegate.domain.transactions import TransactionRequest
from zonegate.integrations.nokia.client import NokiaClientProtocol
from zonegate.integrations.nokia.models import (
    CamaraDeviceSwapResponse,
    CamaraLocationVerificationResponse,
    CamaraNumberVerificationResponse,
    CamaraReachabilityResponse,
    CamaraSimSwapResponse,
)
from zonegate.storage.zova import ZoneGateStore


class FakeNokiaClient:
    """Deterministic in-memory test double for Nokia CAMARA network client."""

    def __init__(
        self,
        number_verified: bool = True,
        location_verified: bool = True,
        sim_swapped: bool = False,
        device_swapped: bool = False,
        reachability_status: str = "CONNECTED_DATA",
    ) -> None:
        self.number_verified = number_verified
        self.location_verified = location_verified
        self.sim_swapped = sim_swapped
        self.device_swapped = device_swapped
        self.reachability_status = reachability_status
        self.calls: list[str] = []

    async def verify_number(self, phone_number: str) -> CamaraNumberVerificationResponse:
        self.calls.append(f"verify_number:{phone_number}")
        return CamaraNumberVerificationResponse(devicePhoneNumberVerified=self.number_verified)

    async def verify_location(
        self,
        phone_number: str,
        latitude: float,
        longitude: float,
        radius_meters: int,
    ) -> CamaraLocationVerificationResponse:
        self.calls.append(f"verify_location:{phone_number}:{latitude},{longitude}")
        res = "TRUE" if self.location_verified else "FALSE"
        return CamaraLocationVerificationResponse(verificationResult=res)

    async def check_sim_swap(self, phone_number: str, max_age_hours: int = 240) -> CamaraSimSwapResponse:
        self.calls.append(f"check_sim_swap:{phone_number}")
        return CamaraSimSwapResponse(swapped=self.sim_swapped)

    async def check_device_swap(self, phone_number: str, max_age_hours: int = 240) -> CamaraDeviceSwapResponse:
        self.calls.append(f"check_device_swap:{phone_number}")
        return CamaraDeviceSwapResponse(swapped=self.device_swapped)

    async def get_reachability(self, phone_number: str) -> CamaraReachabilityResponse:
        self.calls.append(f"get_reachability:{phone_number}")
        return CamaraReachabilityResponse(reachabilityStatus=self.reachability_status)  # type: ignore


@pytest.fixture
def fake_nokia_client() -> FakeNokiaClient:
    return FakeNokiaClient()


@pytest.fixture
def memory_store() -> ZoneGateStore:
    store = ZoneGateStore.create_in_memory()
    yield store
    store.close()


@pytest.fixture
def standard_actor() -> Actor:
    return Actor(
        actor_id="usr_cargo_operator_01",
        role="CARGO_OPERATOR",
        permissions=["cargo:release"],
        registered_phone_number="+14155550199",
        registered_device_id="dev_imei_99887766",
        enrollment_status="ACTIVE",
    )


@pytest.fixture
def standard_device_binding(standard_actor: Actor) -> DeviceBinding:
    return DeviceBinding(
        actor_id=standard_actor.actor_id,
        phone_number=standard_actor.registered_phone_number,
        device_id=standard_actor.registered_device_id,
        bound_at=datetime.now(timezone.utc),
        is_active=True,
    )


@pytest.fixture
def standard_transaction(standard_actor: Actor) -> TransactionRequest:
    return TransactionRequest(
        transaction_id="tx_test_001",
        actor_id=standard_actor.actor_id,
        action="RELEASE_CARGO",
        resource_id="cargo_container_99",
        zone="ZONE_CARGO_BAY_1",
        timestamp=datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc),  # 14:30 UTC
        value=Decimal("15000.00"),
        metadata={"shipping_line": "Maersk"},
    )

@pytest.fixture(autouse=True)
def isolate_from_external_providers(monkeypatch):
    """Keeps the suite off the network, whatever the developer's `.env` says.

    `Settings` reads `.env`, so a machine configured with a live Gemini key and
    a real Nokia base URL would otherwise make billed API calls from a test run
    — slowly, and with results that depend on someone else's service. These
    overrides win over the file, so every test resolves the same way on every
    machine.
    """
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:59999")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("NOKIA_BASE_URL", "http://127.0.0.1:59998")
    monkeypatch.setenv("NOKIA_API_KEY", "test-key")
    monkeypatch.setenv("NOKIA_MCP_ENABLED", "false")
    monkeypatch.setenv("ZOVA_DB_PATH", ":memory:")


def install_fake_nokia(app, nokia_client) -> None:
    """Points a running app's evidence gateway at a deterministic test double.

    The app builds its own live client at startup; swapping it here lets an
    HTTP-level test exercise APPROVE and HOLD paths, which are unreachable
    while every carrier call fails.
    """
    app.state.gateway._nokia_client = nokia_client


async def sign_in_authority(
    client,
    store,
    role: str = "ROLE_CARGO_SUPERVISOR",
    actor_id: str | None = None,
    password: str = "console-test-pass",
) -> str:
    """Enrols a console authority and signs the test client in as them.

    Enrolment, permission edits, policy changes and hold resolution all need a
    console session now; the client keeps the cookie for the rest of the test.
    """
    from zonegate.authorization.console import ConsoleAuthService

    actor_id = actor_id or f"usr_{role.lower().removeprefix('role_')}_test"
    await store.save_actor(
        Actor(
            actor_id=actor_id,
            role=role,
            permissions=["hold:resolve"],
            registered_phone_number="+14155550100",
            registered_device_id="console_workstation",
            enrollment_status="ACTIVE",
        )
    )
    await ConsoleAuthService(store).set_password(actor_id, password)
    response = await client.post("/v1/auth/login", json={"actor_id": actor_id, "password": password})
    assert response.status_code == 200, response.text
    return actor_id
