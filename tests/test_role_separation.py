"""Cargo personnel work in the field; authorities work on the console.

The mobile app is where a release is requested and the console is where a
hold is decided. These tests pin the line between them: an operator cannot
get a console session even with the right password, the console actions that
change outcomes need a signed-in authority, and a hold can only be settled by
the role the policy engine handed it to -- recorded under that person's id,
not a name the client typed.
"""

from datetime import datetime, timedelta, timezone

import pytest
from litestar.testing import AsyncTestClient

from zonegate.app import create_app
from zonegate.authorization.console import SESSION_COOKIE, ConsoleAuthService
from zonegate.authorization.roles import holds_authority, is_field_role, normalize_role
from zonegate.config import Settings
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.credentials import ConsoleSession

from conftest import FakeNokiaClient, install_fake_nokia, sign_in_authority


def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
        NOKIA_MCP_ENABLED=False,
        LLM_PROVIDER="ollama",
        OLLAMA_BASE_URL="http://127.0.0.1:59999",
        DEMO_OPERATOR_PASSWORD="",
    )


async def enrol_operator(store, password: str | None = "field-operator-1") -> Actor:
    actor = Actor(
        actor_id="usr_cargo_operator_01",
        role="CARGO_OPERATOR",
        permissions=["cargo:release"],
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
    if password:
        await ConsoleAuthService(store).set_password(actor.actor_id, password)
    return actor


async def held_for_supervisor(client, app) -> dict:
    """A HIGH_VALUE release: held for ROLE_CARGO_SUPERVISOR."""
    install_fake_nokia(app, FakeNokiaClient())
    await enrol_operator(app.state.store, password=None)
    decision = (
        await client.post(
            "/v1/authorizations",
            json={
                "transaction_id": "tx_night",
                "actor_id": "usr_cargo_operator_01",
                "action": "RELEASE_CARGO",
                "resource_id": "CT-700",
                "zone": "PORT_GATE_17",
                "timestamp": "2026-09-10T03:30:00Z",
                "value": "15000.00",
                "category": "HIGH_VALUE",
            },
        )
    ).json()["decision"]
    assert decision["decision"] == "HOLD"
    assert decision["required_authority"] == "ROLE_CARGO_SUPERVISOR"
    return decision


# ---------------------------------------------------------------------------
# The role vocabulary
# ---------------------------------------------------------------------------


def test_the_role_prefix_and_case_do_not_change_the_job():
    assert normalize_role("ROLE_CARGO_OPERATOR") == "CARGO_OPERATOR"
    assert normalize_role(" role_security_officer ") == "SECURITY_OFFICER"
    assert normalize_role(None) == ""


def test_only_cargo_operators_are_field_personnel():
    assert is_field_role("CARGO_OPERATOR")
    assert is_field_role("ROLE_CARGO_OPERATOR")
    assert not is_field_role("ROLE_CARGO_SUPERVISOR")
    assert not is_field_role("ROLE_SECURITY_OFFICER")


def test_a_hold_belongs_to_the_role_it_names_and_nobody_else():
    assert holds_authority("ROLE_SECURITY_OFFICER", "ROLE_SECURITY_OFFICER")
    assert holds_authority("SECURITY_OFFICER", "ROLE_SECURITY_OFFICER")
    assert not holds_authority("ROLE_CARGO_SUPERVISOR", "ROLE_SECURITY_OFFICER")
    assert not holds_authority("ROLE_SECURITY_OFFICER", None)


# ---------------------------------------------------------------------------
# Console sign-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cargo_operator_cannot_sign_in_to_the_console_even_with_the_right_password():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol_operator(app.state.store)

        response = await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_cargo_operator_01", "password": "field-operator-1"},
        )

        assert response.status_code == 401
        assert "mobile app" in response.json()["detail"]
        assert SESSION_COOKIE not in response.cookies
        assert (await client.get("/v1/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_a_wrong_password_for_an_operator_still_gets_the_generic_message():
    """The role message comes only after the password, so it reveals nothing."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol_operator(app.state.store)

        response = await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_cargo_operator_01", "password": "not-the-password"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Employee ID or password is incorrect"


@pytest.mark.asyncio
async def test_an_operator_session_opened_before_the_rule_stands_for_nobody():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol_operator(app.state.store)
        now = datetime.now(timezone.utc)
        await app.state.store.save_console_session(
            ConsoleSession(
                session_id="old-operator-session",
                actor_id="usr_cargo_operator_01",
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        client.cookies.set(SESSION_COOKIE, "old-operator-session")

        assert (await client.get("/v1/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_an_authority_signs_in_to_the_console():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store, role="ROLE_SECURITY_OFFICER")

        session = (await client.get("/v1/auth/session")).json()

        assert session["actor"]["role"] == "ROLE_SECURITY_OFFICER"


# ---------------------------------------------------------------------------
# Console-only actions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_console_actions_need_a_signed_in_authority():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol_operator(app.state.store, password=None)

        config = await client.put(
            "/v1/policy/config",
            json={"restricted_categories": {}},
        )
        enrolment = await client.post(
            "/v1/actors",
            json={
                "actor": {
                    "actor_id": "usr_uninvited",
                    "role": "ROLE_CARGO_SUPERVISOR",
                    "permissions": [],
                    "registered_phone_number": "+14155550123",
                    "registered_device_id": "dev_x",
                    "enrollment_status": "ACTIVE",
                }
            },
        )
        permissions = await client.put(
            "/v1/actors/usr_cargo_operator_01/permissions",
            json={"permissions": [], "expected_permissions": ["cargo:release"]},
        )
        resolution = await client.post(
            "/v1/authorizations/dec_any/resolve",
            json={"outcome": "APPROVE"},
        )

        assert config.status_code == 401
        assert enrolment.status_code == 401
        assert permissions.status_code == 401
        assert resolution.status_code == 401
        assert await app.state.store.get_actor("usr_uninvited") is None
        stored = await app.state.store.get_actor("usr_cargo_operator_01")
        assert stored.permissions == ["cargo:release"]


@pytest.mark.asyncio
async def test_reading_decisions_needs_no_session_so_the_mobile_app_keeps_working():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await held_for_supervisor(client, app)

        assert (await client.get("/v1/authorizations")).status_code == 200
        assert (await client.get("/v1/authorizations/contexts")).status_code == 200
        assert (await client.get("/v1/actors/usr_cargo_operator_01")).status_code == 200


# ---------------------------------------------------------------------------
# Hold resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_hold_cannot_be_resolved_by_a_different_authority():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        held = await held_for_supervisor(client, app)
        await sign_in_authority(client, app.state.store, role="ROLE_SECURITY_OFFICER")

        response = await client.post(
            f"/v1/authorizations/{held['decision_id']}/resolve",
            json={"outcome": "APPROVE", "note": "not mine to approve"},
        )

        assert response.status_code == 403
        assert "ROLE_CARGO_SUPERVISOR" in response.json()["detail"]
        stored = await app.state.store.get_decision(held["decision_id"])
        assert stored.resolution is None


@pytest.mark.asyncio
async def test_the_named_authority_resolves_it_under_their_own_id():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        held = await held_for_supervisor(client, app)
        supervisor = await sign_in_authority(
            client, app.state.store, role="ROLE_CARGO_SUPERVISOR", actor_id="usr_night_supervisor"
        )

        response = await client.post(
            f"/v1/authorizations/{held['decision_id']}/resolve",
            json={"outcome": "APPROVE", "resolved_by": "someone-else-entirely", "note": "checked"},
        )

        assert response.status_code == 201
        resolution = response.json()["decision"]["resolution"]
        assert resolution["resolved_by"] == supervisor
        assert resolution["authority_role"] == "ROLE_CARGO_SUPERVISOR"


@pytest.mark.asyncio
async def test_an_authority_enrolled_without_the_role_prefix_still_holds_the_role():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        held = await held_for_supervisor(client, app)
        await sign_in_authority(client, app.state.store, role="CARGO_SUPERVISOR")

        response = await client.post(
            f"/v1/authorizations/{held['decision_id']}/resolve",
            json={"outcome": "DENY", "note": "no manifest"},
        )

        assert response.status_code == 201
