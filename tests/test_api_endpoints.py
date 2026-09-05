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
