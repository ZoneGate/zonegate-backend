"""The endpoints the operations console added after the review meeting.

Permission editing, the cargo category vocabulary, and the geofences the map
draws. Each is a screen that would otherwise show invented data, so what is
checked here is mainly that the numbers on screen are the ones the pipeline
actually used.
"""

from datetime import datetime, timezone

import pytest
from litestar.testing import AsyncTestClient

from zonegate.app import create_app
from zonegate.config import Settings
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.evidence.gateway import DEFAULT_ZONE_REGISTRY

from conftest import FakeNokiaClient, install_fake_nokia


def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
        NOKIA_MCP_ENABLED=False,
        LLM_PROVIDER="ollama",
        OLLAMA_BASE_URL="http://127.0.0.1:59999",
        DEMO_OPERATOR_PASSWORD="",
    )


async def enrol(store, permissions: list[str] | None = None) -> Actor:
    actor = Actor(
        actor_id="usr_cargo_operator_01",
        role="ROLE_CARGO_OPERATOR",
        permissions=["cargo:release"] if permissions is None else permissions,
        registered_phone_number="+14155550199",
        registered_device_id="dev_imei_99887766",
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


# ---------------------------------------------------------------------------
# Permission editing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permissions_can_be_replaced_and_are_read_back():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store)

        response = await client.put(
            "/v1/actors/usr_cargo_operator_01/permissions",
            json={
                "permissions": ["cargo:release", "cargo:inspect"],
                "expected_permissions": ["cargo:release"],
            },
        )

        assert response.status_code == 200
        assert response.json()["permissions"] == ["cargo:inspect", "cargo:release"]

        reread = (await client.get("/v1/actors/usr_cargo_operator_01")).json()
        assert reread["actor"]["permissions"] == ["cargo:inspect", "cargo:release"]


@pytest.mark.asyncio
async def test_a_permission_change_is_refused_when_the_stored_set_moved_on():
    """Two editors on one employee must not silently overwrite each other."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store)

        response = await client.put(
            "/v1/actors/usr_cargo_operator_01/permissions",
            json={
                "permissions": ["cargo:inspect"],
                "expected_permissions": ["cargo:release", "cargo:audit"],
            },
        )

        assert response.status_code == 409
        stored = (await client.get("/v1/actors/usr_cargo_operator_01")).json()
        assert stored["actor"]["permissions"] == ["cargo:release"]


@pytest.mark.asyncio
async def test_removing_the_release_permission_turns_the_next_request_into_a_deny():
    """The point of the screen: it changes what the policy engine decides."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)

        await client.put(
            "/v1/actors/usr_cargo_operator_01/permissions",
            json={"permissions": [], "expected_permissions": ["cargo:release"]},
        )

        decision = (
            await client.post(
                "/v1/authorizations",
                json={
                    "transaction_id": "tx_after_revoke",
                    "actor_id": "usr_cargo_operator_01",
                    "action": "RELEASE_CARGO",
                    "resource_id": "CT-700",
                    "zone": "PORT_GATE_17",
                    "timestamp": "2026-09-10T14:30:00Z",
                    "value": "15000.00",
                    "category": "GENERAL",
                },
            )
        ).json()["decision"]

        assert decision["decision"] == "DENY"
        assert "cargo:release" in decision["reasons"][0]


@pytest.mark.asyncio
async def test_permissions_are_deduplicated_and_blanks_dropped():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store)

        response = await client.put(
            "/v1/actors/usr_cargo_operator_01/permissions",
            json={
                "permissions": ["cargo:release", " cargo:release ", "  ", "cargo:inspect"],
                "expected_permissions": ["cargo:release"],
            },
        )

        assert response.json()["permissions"] == ["cargo:inspect", "cargo:release"]


@pytest.mark.asyncio
async def test_editing_permissions_of_an_actor_who_is_not_enrolled_is_404():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        response = await client.put(
            "/v1/actors/usr_ghost/permissions",
            json={"permissions": ["cargo:release"], "expected_permissions": []},
        )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Enrolment with a console password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_employee_enrolled_with_a_password_can_sign_in_immediately():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        enrolled = await client.post(
            "/v1/actors",
            json={
                "actor": {
                    "actor_id": "usr_new_supervisor",
                    "role": "ROLE_CARGO_SUPERVISOR",
                    "permissions": ["cargo:release"],
                    "registered_phone_number": "+14155550111",
                    "registered_device_id": "dev_imei_11223344",
                    "enrollment_status": "ACTIVE",
                },
                "password": "let-me-in-please",
            },
        )
        assert enrolled.status_code == 201

        signed_in = await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_new_supervisor", "password": "let-me-in-please"},
        )
        assert signed_in.status_code == 200


@pytest.mark.asyncio
async def test_an_employee_enrolled_without_a_password_is_still_bound_for_the_pipeline():
    """A field operator uses the mobile app; they are not a console user."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/v1/actors",
            json={
                "actor": {
                    "actor_id": "usr_field_only",
                    "role": "ROLE_CARGO_OPERATOR",
                    "permissions": ["cargo:release"],
                    "registered_phone_number": "+14155550222",
                    "registered_device_id": "dev_imei_55667788",
                    "enrollment_status": "ACTIVE",
                }
            },
        )

        assert response.status_code == 201
        assert response.json()["binding"]["is_active"] is True
        assert (
            await client.post(
                "/v1/auth/login",
                json={"actor_id": "usr_field_only", "password": "anything"},
            )
        ).status_code == 401


# ---------------------------------------------------------------------------
# Cargo categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_category_list_marks_which_ones_escalate_and_to_whom():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        options = {row["category"]: row for row in (await client.get("/v1/policy/categories")).json()}

        assert options["GENERAL"]["restricted"] is False
        assert options["GENERAL"]["required_authority"] is None
        assert options["WEAPONS"]["restricted"] is True
        assert options["WEAPONS"]["required_authority"] == "ROLE_SECURITY_OFFICER"


@pytest.mark.asyncio
async def test_a_category_configured_outside_the_built_in_vocabulary_is_still_listed():
    """A rule in force that the console does not show is a rule nobody can see."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await client.put(
            "/v1/policy/config",
            json={
                "restricted_categories": {"LIVESTOCK": "ROLE_VETERINARY_LEAD"},
                "window_start_hour": 6,
                "window_end_hour": 20,
            },
        )

        options = {row["category"]: row for row in (await client.get("/v1/policy/categories")).json()}

        assert options["LIVESTOCK"]["required_authority"] == "ROLE_VETERINARY_LEAD"
        assert options["WEAPONS"]["restricted"] is False


# ---------------------------------------------------------------------------
# Geofences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_zone_list_is_the_registry_the_gateway_actually_checks_against():
    """The console map has to draw the circle the carrier was asked about."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        zones = {row["zone"]: row for row in (await client.get("/v1/policy/zones")).json()}

        assert set(zones) == set(DEFAULT_ZONE_REGISTRY)
        for name, (lat, lon, radius) in DEFAULT_ZONE_REGISTRY.items():
            assert zones[name]["latitude"] == lat
            assert zones[name]["longitude"] == lon
            assert zones[name]["radius_meters"] == radius


@pytest.mark.asyncio
async def test_a_transaction_records_the_category_it_was_decided_under():
    """The console reads the category back off the stored transaction."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)

        posted = await client.post(
            "/v1/authorizations",
            json={
                "transaction_id": "tx_category_recorded",
                "actor_id": "usr_cargo_operator_01",
                "action": "RELEASE_CARGO",
                "resource_id": "CT-700",
                "zone": "ZONE_CARGO_BAY_1",
                "timestamp": "2026-09-10T14:30:00Z",
                "value": "15000.00",
                "category": "HAZARDOUS",
            },
        )
        decision_id = posted.json()["decision"]["decision_id"]

        context = (await client.get(f"/v1/authorizations/{decision_id}/context")).json()

        assert context["transaction"]["category"] == "HAZARDOUS"


@pytest.mark.asyncio
async def test_a_transaction_posted_without_a_category_defaults_to_general():
    """The mobile app in the field predates the field; it must keep working."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)

        posted = await client.post(
            "/v1/authorizations",
            json={
                "transaction_id": "tx_no_category",
                "actor_id": "usr_cargo_operator_01",
                "action": "RELEASE_CARGO",
                "resource_id": "CT-700",
                "zone": "ZONE_CARGO_BAY_1",
                "timestamp": "2026-09-10T14:30:00Z",
                "value": "15000.00",
            },
        )
        decision_id = posted.json()["decision"]["decision_id"]

        context = (await client.get(f"/v1/authorizations/{decision_id}/context")).json()

        assert context["transaction"]["category"] == "GENERAL"
