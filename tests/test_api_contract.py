"""The HTTP contract both clients depend on.

The dashboard and the mobile app are separate codebases that only agree with
each other through this surface. Every status code, filter and payload shape
they rely on is asserted here, including the failure paths — a console that
renders a 500 as an empty queue is worse than one that says it cannot reach
the backend.
"""

from datetime import datetime, timezone

import pytest
from litestar.testing import AsyncTestClient
from zonegate.app import create_app
from zonegate.config import Settings
from zonegate.domain.actors import Actor, DeviceBinding

from conftest import FakeNokiaClient, install_fake_nokia, sign_in_authority


def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
        LLM_PROVIDER="ollama",
        OLLAMA_BASE_URL="http://127.0.0.1:59999",
        NOKIA_BASE_URL="http://127.0.0.1:59998",
        NOKIA_MCP_ENABLED=False,
        CORS_ALLOW_ORIGINS="http://localhost:3000",
    )


def carrier(app, **overrides) -> None:
    """Gives the running app a deterministic carrier so evidence can succeed.

    Without this every CAMARA call fails and the pipeline can only ever answer
    DENY, which would hide the APPROVE and HOLD paths these tests exist for.
    """
    install_fake_nokia(app, FakeNokiaClient(**overrides))


async def enrol(store, actor_id: str = "usr_cargo_operator_01") -> Actor:
    actor = Actor(
        actor_id=actor_id,
        role="ROLE_CARGO_OPERATOR",
        permissions=["cargo:release"],
        registered_phone_number="+14155550199",
        registered_device_id=f"dev_{actor_id}",
        enrollment_status="ACTIVE",
    )
    await store.save_actor(actor)
    await store.save_device_binding(
        DeviceBinding(
            actor_id=actor.actor_id,
            phone_number=actor.registered_phone_number,
            device_id=actor.registered_device_id,
            bound_at=datetime.now(timezone.utc),
            is_active=True,
        )
    )
    return actor


def transaction_payload(
    transaction_id: str,
    value: str = "15000.00",
    hour: int = 14,
    actor_id: str = "usr_cargo_operator_01",
    category: str = "GENERAL",
) -> dict:
    return {
        "transaction_id": transaction_id,
        "actor_id": actor_id,
        "action": "RELEASE_CARGO",
        "resource_id": "CT-700",
        "zone": "PORT_GATE_17",
        "timestamp": f"2026-09-10T{hour:02d}:30:00Z",
        "value": value,
        "category": category,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_reports_each_dependency_by_name():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        body = (await client.get("/health")).json()

        assert body["status"] == "healthy"
        assert "zova_persistence" in body["dependencies"]
        assert "nokia_camara_gateway" in body["dependencies"]


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrolling_an_actor_returns_the_binding_it_created():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        response = await client.post(
            "/v1/actors",
            json={
                "actor": {
                    "actor_id": "usr_new",
                    "role": "ROLE_GATE_CLERK",
                    "permissions": ["cargo:inspect"],
                    "registered_phone_number": "+14155550144",
                    "registered_device_id": "dev_new",
                    "enrollment_status": "ACTIVE",
                }
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["binding"]["device_id"] == "dev_new"
        assert body["binding"]["is_active"] is True


@pytest.mark.asyncio
async def test_enrolment_can_bind_a_device_other_than_the_registered_one():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        body = (
            await client.post(
                "/v1/actors",
                json={
                    "actor": {
                        "actor_id": "usr_swap",
                        "role": "ROLE_CARGO_OPERATOR",
                        "permissions": ["cargo:release"],
                        "registered_phone_number": "+14155550199",
                        "registered_device_id": "dev_original",
                        "enrollment_status": "ACTIVE",
                    },
                    "device_id": "dev_replacement",
                },
            )
        ).json()

        assert body["binding"]["device_id"] == "dev_replacement"


@pytest.mark.asyncio
async def test_unknown_actor_lookup_is_a_404_not_an_empty_record():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        response = await client.get("/v1/actors/usr_missing")

        assert response.status_code == 404
        assert "not enrolled" in response.json()["detail"]


@pytest.mark.asyncio
async def test_an_actor_without_a_binding_is_a_404_on_direct_lookup():
    """The lookup answers the question 'can this actor act', not 'does it exist'."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await app.state.store.save_actor(
            Actor(
                actor_id="usr_unbound",
                role="ROLE_CARGO_OPERATOR",
                permissions=["cargo:release"],
                registered_phone_number="+14155550199",
                registered_device_id="dev_x",
                enrollment_status="ACTIVE",
            )
        )

        response = await client.get("/v1/actors/usr_unbound")

        assert response.status_code == 404
        assert "no active device binding" in response.json()["detail"]


@pytest.mark.asyncio
async def test_enrolment_rejects_unknown_fields():
    """`extra="forbid"` keeps a typo from being silently accepted."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        response = await client.post(
            "/v1/actors",
            json={
                "actor": {
                    "actor_id": "usr_typo",
                    "role": "ROLE_CARGO_OPERATOR",
                    "permissions": [],
                    "registered_phone_number": "+1",
                    "registered_device_id": "d",
                    "enrollment_status": "ACTIVE",
                    "clearance_level": "L3",
                }
            },
        )

        assert response.status_code >= 400


@pytest.mark.asyncio
async def test_roster_limit_is_clamped_rather_than_trusted():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store, "usr_a")
        await enrol(app.state.store, "usr_b")

        assert (await client.get("/v1/actors?limit=99999")).status_code == 200
        assert (await client.get("/v1/actors?limit=0")).status_code == 200
        assert len((await client.get("/v1/actors?limit=1")).json()) == 1


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unenrolled_actor_is_denied_rather_than_erroring():
    """The pipeline answers DENY; it must not 500 on an unknown actor."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/v1/authorizations",
            json=transaction_payload("tx_unknown", actor_id="usr_ghost"),
        )

        assert response.status_code == 201
        assert response.json()["decision"]["decision"] == "DENY"


@pytest.mark.asyncio
async def test_a_decision_can_be_read_back_by_its_id():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        carrier(app)
        await enrol(app.state.store)

        created = (
            await client.post("/v1/authorizations", json=transaction_payload("tx_read"))
        ).json()["decision"]

        fetched = (
            await client.get(f"/v1/authorizations/{created['decision_id']}")
        ).json()

        assert fetched["decision_id"] == created["decision_id"]
        assert fetched["transaction_id"] == "tx_read"


@pytest.mark.asyncio
async def test_reading_an_unknown_decision_is_a_404():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        response = await client.get("/v1/authorizations/dec_nope")

        assert response.status_code == 404


@pytest.mark.asyncio
async def test_listing_returns_newest_first():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        carrier(app)
        await enrol(app.state.store)

        for index in range(3):
            await client.post(
                "/v1/authorizations", json=transaction_payload(f"tx_order_{index}")
            )

        body = (await client.get("/v1/authorizations?limit=10")).json()

        assert [row["transaction_id"] for row in body] == [
            "tx_order_2",
            "tx_order_1",
            "tx_order_0",
        ]


@pytest.mark.asyncio
async def test_listing_filters_by_outcome():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        carrier(app)
        await enrol(app.state.store)

        await client.post("/v1/authorizations", json=transaction_payload("tx_ok"))
        await client.post(
            "/v1/authorizations",
            json=transaction_payload("tx_held", value="250000.00", hour=3),
        )

        holds = (await client.get("/v1/authorizations?decision=HOLD")).json()

        assert [row["transaction_id"] for row in holds] == ["tx_held"]
        assert all(row["decision"] == "HOLD" for row in holds)


@pytest.mark.asyncio
async def test_pending_filter_excludes_a_hold_once_it_is_resolved():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        carrier(app)
        await enrol(app.state.store)

        held = (
            await client.post(
                "/v1/authorizations",
                json=transaction_payload("tx_pending", value="250000.00", hour=3),
            )
        ).json()["decision"]

        before = (
            await client.get("/v1/authorizations?decision=HOLD&pending=true")
        ).json()
        assert len(before) == 1

        await client.post(
            f"/v1/authorizations/{held['decision_id']}/resolve",
            json={
                "outcome": "APPROVE",
                "resolved_by": "usr_cargo_supervisor_02",
                "note": "cleared",
            },
        )

        after = (
            await client.get("/v1/authorizations?decision=HOLD&pending=true")
        ).json()
        assert after == []


@pytest.mark.asyncio
async def test_an_out_of_range_limit_does_not_error():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        assert (await client.get("/v1/authorizations?limit=100000")).status_code == 200
        assert (await client.get("/v1/authorizations?limit=0")).status_code == 200


@pytest.mark.asyncio
async def test_an_invalid_outcome_filter_is_rejected():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        response = await client.get("/v1/authorizations?decision=MAYBE")

        assert response.status_code >= 400


# ---------------------------------------------------------------------------
# Decision context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_carries_the_transaction_and_evidence_behind_a_decision():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        carrier(app)
        await enrol(app.state.store)

        created = (
            await client.post(
                "/v1/authorizations", json=transaction_payload("tx_ctx")
            )
        ).json()["decision"]

        body = (
            await client.get(
                f"/v1/authorizations/{created['decision_id']}/context"
            )
        ).json()

        assert body["decision"]["decision_id"] == created["decision_id"]
        assert body["transaction"]["resource_id"] == "CT-700"
        assert body["receipt"]["transaction_id"] == "tx_ctx"


@pytest.mark.asyncio
async def test_context_for_an_unknown_decision_is_a_404():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        response = await client.get("/v1/authorizations/dec_nope/context")

        assert response.status_code == 404


@pytest.mark.asyncio
async def test_bulk_contexts_match_the_single_context_endpoint():
    """The console reads the list form; it must not disagree with the detail."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        carrier(app)
        await enrol(app.state.store)

        created = (
            await client.post(
                "/v1/authorizations", json=transaction_payload("tx_bulk")
            )
        ).json()["decision"]

        listed = (await client.get("/v1/authorizations/contexts?limit=10")).json()
        single = (
            await client.get(
                f"/v1/authorizations/{created['decision_id']}/context"
            )
        ).json()

        assert len(listed) == 1
        assert listed[0]["decision"]["decision_id"] == single["decision"]["decision_id"]
        assert listed[0]["transaction"] == single["transaction"]


@pytest.mark.asyncio
async def test_bulk_contexts_honour_the_same_filters_as_the_plain_listing():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        carrier(app)
        await enrol(app.state.store)

        await client.post("/v1/authorizations", json=transaction_payload("tx_c_ok"))
        await client.post(
            "/v1/authorizations",
            json=transaction_payload("tx_c_hold", value="250000.00", hour=3),
        )

        holds = (
            await client.get("/v1/authorizations/contexts?decision=HOLD&pending=true")
        ).json()

        assert len(holds) == 1
        assert holds[0]["decision"]["transaction_id"] == "tx_c_hold"


# ---------------------------------------------------------------------------
# Hold resolution over HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolving_a_hold_over_http_returns_the_updated_record():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        carrier(app)
        await enrol(app.state.store)

        held = (
            await client.post(
                "/v1/authorizations",
                json=transaction_payload("tx_http_hold", value="250000.00", hour=3),
            )
        ).json()["decision"]

        response = await client.post(
            f"/v1/authorizations/{held['decision_id']}/resolve",
            json={
                "outcome": "APPROVE",
                "resolved_by": "usr_cargo_supervisor_02",
                "note": "Confirmed by radio",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["decision"]["resolution"]["outcome"] == "APPROVE"
        assert body["receipt"]["token"] is not None


@pytest.mark.asyncio
async def test_resolving_a_deny_over_http_is_a_409():
    """A conflict, not a validation error: the request was well-formed."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        carrier(app)
        await enrol(app.state.store)

        # An actor without the permission produces a deterministic DENY.
        denied = (
            await client.post(
                "/v1/authorizations",
                json=transaction_payload("tx_http_deny", actor_id="usr_ghost"),
            )
        ).json()["decision"]

        response = await client.post(
            f"/v1/authorizations/{denied['decision_id']}/resolve",
            json={"outcome": "APPROVE", "resolved_by": "usr_cargo_supervisor_02"},
        )

        assert response.status_code == 409


@pytest.mark.asyncio
async def test_resolving_an_unknown_decision_over_http_is_a_404():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        response = await client.post(
            "/v1/authorizations/dec_missing/resolve",
            json={"outcome": "APPROVE", "resolved_by": "usr_cargo_supervisor_02"},
        )

        assert response.status_code == 404


@pytest.mark.asyncio
async def test_resolution_rejects_an_unknown_outcome():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        carrier(app)
        await enrol(app.state.store)

        held = (
            await client.post(
                "/v1/authorizations",
                json=transaction_payload("tx_bad_outcome", value="250000.00", hour=3),
            )
        ).json()["decision"]

        response = await client.post(
            f"/v1/authorizations/{held['decision_id']}/resolve",
            json={"outcome": "MAYBE", "resolved_by": "usr_cargo_supervisor_02"},
        )

        assert response.status_code >= 400


# ---------------------------------------------------------------------------
# Policy configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_config_rejects_an_hour_outside_the_clock():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        response = await client.put(
            "/v1/policy/config",
            json={
                "restricted_categories": {"WEAPONS": "ROLE_SECURITY_OFFICER"},
                "window_start_hour": 25,
                "window_end_hour": 20,
            },
        )

        assert response.status_code >= 400


@pytest.mark.asyncio
async def test_policy_config_rejects_a_restricted_category_with_no_authority():
    """A category with nobody to escalate to would release while looking restricted."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        response = await client.put(
            "/v1/policy/config",
            json={
                "restricted_categories": {"WEAPONS": ""},
                "window_start_hour": 6,
                "window_end_hour": 20,
            },
        )

        assert response.status_code >= 400


@pytest.mark.asyncio
async def test_a_saved_category_map_changes_the_next_decision_over_http():
    """End to end: the console saves, and the very next request is judged by it."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        carrier(app)
        await enrol(app.state.store)

        first = (
            await client.post(
                "/v1/authorizations",
                json=transaction_payload("tx_cfg_before", category="HAZARDOUS"),
            )
        ).json()["decision"]
        assert first["decision"] == "HOLD"
        assert first["required_authority"] == "ROLE_SAFETY_OFFICER"

        await client.put(
            "/v1/policy/config",
            json={
                "restricted_categories": {"WEAPONS": "ROLE_SECURITY_OFFICER"},
                "window_start_hour": 6,
                "window_end_hour": 20,
            },
        )

        second = (
            await client.post(
                "/v1/authorizations",
                json=transaction_payload("tx_cfg_after", category="HAZARDOUS"),
            )
        ).json()["decision"]

        assert second["decision"] == "APPROVE"


@pytest.mark.asyncio
async def test_retuning_policy_does_not_rewrite_decisions_already_recorded():
    """History has to keep the reason it was actually given."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        carrier(app)
        await enrol(app.state.store)

        held = (
            await client.post(
                "/v1/authorizations",
                json=transaction_payload("tx_frozen", value="150000.00", hour=3),
            )
        ).json()["decision"]

        await client.put(
            "/v1/policy/config",
            json={
                "restricted_categories": {},
                "window_start_hour": 0,
                "window_end_hour": 24,
            },
        )

        reread = (
            await client.get(f"/v1/authorizations/{held['decision_id']}")
        ).json()

        assert reread["decision"] == "HOLD"
        assert reread["reasons"] == held["reasons"]


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_browser_methods_the_console_uses_are_all_permitted():
    """A missing method here fails only in a browser, never in a test client."""
    app = create_app(settings())
    cors = app.cors_config

    assert cors is not None
    for method in ("GET", "POST", "PUT", "OPTIONS"):
        assert method in cors.allow_methods

    assert "http://localhost:3000" in cors.allow_origins
