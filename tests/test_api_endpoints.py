from datetime import datetime, timezone
from decimal import Decimal
import pytest
from litestar.testing import AsyncTestClient
from zonegate.app import create_app
from zonegate.config import Settings
from zonegate.domain.actors import Actor, DeviceBinding


@pytest.mark.asyncio
async def test_api_authorization_and_receipt_endpoints():
    test_settings = Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
        OLLAMA_BASE_URL="http://127.0.0.1:59999",
        NOKIA_BASE_URL="http://127.0.0.1:59998",
    )
    app = create_app(test_settings)

    async with AsyncTestClient(app=app) as client:
        # 1. Seed actor and binding directly into store
        store = app.state.store
        actor = Actor(
            actor_id="actor_api_test",
            role="CARGO_OPERATOR",
            permissions=["cargo:release"],
            registered_phone_number="+14155550199",
            registered_device_id="device_test_123",
            enrollment_status="ACTIVE",
        )
        binding = DeviceBinding(
            actor_id=actor.actor_id,
            phone_number=actor.registered_phone_number,
            device_id=actor.registered_device_id,
            bound_at=datetime.now(timezone.utc),
            is_active=True,
        )
        await store.save_actor(actor)
        await store.save_device_binding(binding)

        # 2. Issue authorization request
        payload = {
            "transaction_id": "tx_api_001",
            "actor_id": "actor_api_test",
            "action": "RELEASE_CARGO",
            "resource_id": "cont_42",
            "zone": "ZONE_CARGO_BAY_1",
            "timestamp": "2026-09-04T12:00:00Z",
            "value": "15000.00",
            "metadata": {"carrier": "COSCO"},
        }

        # Note: Since Nokia server is not live, evidence collection would fail if real network was called,
        # but in our architecture, EvidenceGateway can use FakeNokiaClient or live NokiaEvidenceClient.
        # Here app uses live NokiaEvidenceClient pointing to non-existent port, so network call fails safely -> DENY
        response = await client.post("/v1/authorizations", json=payload)
        assert response.status_code == 201 or response.status_code == 200
        data = response.json()
        assert "decision" in data
        assert "receipt" in data
        decision_id = data["decision"]["decision_id"]
        receipt_id = data["receipt"]["receipt_id"]

        # 3. GET /v1/authorizations/{decision_id}
        dec_resp = await client.get(f"/v1/authorizations/{decision_id}")
        assert dec_resp.status_code == 200
        assert dec_resp.json()["decision_id"] == decision_id

        # 4. GET /v1/receipts/{receipt_id}
        rcpt_resp = await client.get(f"/v1/receipts/{receipt_id}")
        assert rcpt_resp.status_code == 200
        assert rcpt_resp.json()["receipt_id"] == receipt_id


@pytest.mark.asyncio
async def test_actor_and_device_enrollment_api():
    test_settings = Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
    )
    app = create_app(test_settings)

    async with AsyncTestClient(app=app) as client:
        # 1. Enroll Actor via POST /v1/actors
        actor_data = {
            "actor_id": "usr_custom_operator",
            "role": "SECURITY_OFFICER",
            "permissions": ["cargo:inspect", "vault:access"],
            "registered_phone_number": "+14155559988",
            "registered_device_id": "dev_sec_99",
            "enrollment_status": "ACTIVE",
        }
        res = await client.post("/v1/actors", json=actor_data)
        assert res.status_code in (200, 201)
        assert res.json()["actor_id"] == "usr_custom_operator"

        # 2. Get Actor via GET /v1/actors/{actor_id}
        get_res = await client.get("/v1/actors/usr_custom_operator")
        assert get_res.status_code == 200
        assert get_res.json()["registered_phone_number"] == "+14155559988"

        # 3. Enroll Device Binding via POST /v1/device-bindings
        binding_data = {
            "actor_id": "usr_custom_operator",
            "phone_number": "+14155559988",
            "device_id": "dev_sec_99",
            "bound_at": "2026-09-08T10:00:00Z",
            "is_active": True,
        }
        b_res = await client.post("/v1/device-bindings", json=binding_data)
        assert b_res.status_code in (200, 201)
        assert b_res.json()["device_id"] == "dev_sec_99"

        # 4. Get Device Binding via GET /v1/device-bindings/{actor_id}
        b_get_res = await client.get("/v1/device-bindings/usr_custom_operator")
        assert b_get_res.status_code == 200
        assert b_get_res.json()["device_id"] == "dev_sec_99"


@pytest.mark.asyncio
async def test_unsupported_action_returns_clean_deny_not_500():
    test_settings = Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
    )
    app = create_app(test_settings)

    async with AsyncTestClient(app=app) as client:
        # Default demo actor usr_cargo_operator_01 is auto-seeded on startup
        payload = {
            "transaction_id": "tx_unsupported_action_1",
            "actor_id": "usr_cargo_operator_01",
            "action": "VAULT_ACCESS",  # Action without defined workflow policy
            "resource_id": "vault_main_01",
            "zone": "ZONE_CENTRAL",
            "timestamp": "2026-09-04T12:00:00Z",
            "value": "100.00",
        }
        res = await client.post("/v1/authorizations", json=payload)
        # Must return clean 200/201, NOT HTTP 500
        assert res.status_code in (200, 201)
        data = res.json()
        assert data["decision"]["decision"] == "DENY"
        assert any("VAULT_ACCESS" in r for r in data["decision"]["reasons"])


@pytest.mark.asyncio
async def test_token_claim_endpoint_and_replay_protection():
    test_settings = Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
    )
    app = create_app(test_settings)

    async with AsyncTestClient(app=app) as client:
        store = app.state.store
        token_service = app.state.auth_service.token_service

        # 1. Issue a valid token directly into store
        token = token_service.issue_token(
            actor_id="usr_cargo_operator_01",
            action="RELEASE_CARGO",
            resource_id="cont_valid_99",
            zone="ZONE_PORT_GATE",
            decision_id="dec_test_123",
            ttl_seconds=300,
        )
        await store.save_token(token)

        # 2. Scope mismatch claim attempt (wrong resource)
        mismatch_res = await client.post("/v1/tokens/claim", json={
            "token_id": token.token_id,
            "action": "RELEASE_CARGO",
            "resource_id": "cont_WRONG_99",
            "zone": "ZONE_PORT_GATE",
        })
        assert mismatch_res.status_code in (200, 201)
        mismatch_data = mismatch_res.json()
        assert mismatch_data["claimed"] is False
        assert mismatch_data["status"] == "REJECTED_SCOPE_MISMATCH"

        # 3. Successful valid claim
        claim_res = await client.post("/v1/tokens/claim", json={
            "token_id": token.token_id,
            "action": "RELEASE_CARGO",
            "resource_id": "cont_valid_99",
            "zone": "ZONE_PORT_GATE",
        })
        assert claim_res.status_code in (200, 201)
        claim_data = claim_res.json()
        assert claim_data["claimed"] is True
        assert claim_data["status"] == "CLAIMED"
        assert claim_data["claimed_at"] is not None

        # 4. Replay attempt (second claim must fail with REJECTED_ALREADY_CLAIMED)
        replay_res = await client.post("/v1/tokens/claim", json={
            "token_id": token.token_id,
            "action": "RELEASE_CARGO",
            "resource_id": "cont_valid_99",
            "zone": "ZONE_PORT_GATE",
        })
        assert replay_res.status_code in (200, 201)
        replay_data = replay_res.json()
        assert replay_data["claimed"] is False
        assert replay_data["status"] == "REJECTED_ALREADY_CLAIMED"

        # 5. Token inspection via GET /v1/tokens/{token_id}
        token_inspect_res = await client.get(f"/v1/tokens/{token.token_id}")
        assert token_inspect_res.status_code == 200
        assert token_inspect_res.json()["claimed"] is True
