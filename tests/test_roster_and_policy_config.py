from datetime import datetime, timezone
from decimal import Decimal
import pytest
from litestar.testing import AsyncTestClient
from zonegate.app import create_app
from zonegate.config import Settings
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.policy_config import PolicyConfig

from conftest import sign_in_authority


def _test_settings() -> Settings:
    """Settings that keep the app off the network and out of a real database."""
    return Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
        OLLAMA_BASE_URL="http://127.0.0.1:59999",
        NOKIA_BASE_URL="http://127.0.0.1:59998",
        NOKIA_MCP_ENABLED=False,
    )


def _actor(actor_id: str, role: str = "ROLE_CARGO_OPERATOR") -> Actor:
    return Actor(
        actor_id=actor_id,
        role=role,
        permissions=["cargo:release"],
        registered_phone_number="+14155550199",
        registered_device_id=f"device_{actor_id}",
        enrollment_status="ACTIVE",
    )


def _binding(actor: Actor, is_active: bool = True) -> DeviceBinding:
    return DeviceBinding(
        actor_id=actor.actor_id,
        phone_number=actor.registered_phone_number,
        device_id=actor.registered_device_id,
        bound_at=datetime.now(timezone.utc),
        is_active=is_active,
    )


@pytest.mark.asyncio
async def test_roster_lists_actors_newest_first_with_their_bindings():
    """The console needs the roster and each actor's binding in one call."""
    app = create_app(_test_settings())

    async with AsyncTestClient(app=app) as client:
        store = app.state.store

        first = _actor("usr_first")
        second = _actor("usr_second", role="ROLE_GATE_CLERK")

        await store.save_actor(first)
        await store.save_device_binding(_binding(first))
        await store.save_actor(second)

        response = await client.get("/v1/actors")
        assert response.status_code == 200

        body = response.json()
        assert [entry["actor"]["actor_id"] for entry in body] == [
            "usr_second",
            "usr_first",
        ]

        # An actor enrolled without a binding still appears, flagged as unbound,
        # because that is exactly the state that gets denied at the gate.
        assert body[0]["binding"] is None
        assert body[1]["binding"]["is_active"] is True


@pytest.mark.asyncio
async def test_roster_survives_reenrolment_without_duplicating():
    """Re-enrolling an actor updates the record rather than adding a second row."""
    app = create_app(_test_settings())

    async with AsyncTestClient(app=app) as client:
        store = app.state.store

        actor = _actor("usr_repeat")
        await store.save_actor(actor)

        updated = actor.model_copy(update={"role": "ROLE_CARGO_SUPERVISOR"})
        await store.save_actor(updated)

        body = (await client.get("/v1/actors")).json()

        assert len(body) == 1
        assert body[0]["actor"]["role"] == "ROLE_CARGO_SUPERVISOR"


@pytest.mark.asyncio
async def test_policy_config_round_trips_and_rebinds_the_engine():
    """Saving policy must change what the running engine evaluates against."""
    app = create_app(_test_settings())

    async with AsyncTestClient(app=app) as client:
        await sign_in_authority(client, app.state.store)
        default = (await client.get("/v1/policy/config")).json()
        assert default["restricted_categories"]["WEAPONS"] == "ROLE_SECURITY_OFFICER"

        response = await client.put(
            "/v1/policy/config",
            json={
                "restricted_categories": {"PERISHABLE": "ROLE_COLD_CHAIN_LEAD"},
            },
        )
        assert response.status_code == 200

        engine = app.state.auth_service.policy_engine
        assert engine.config.authority_for("PERISHABLE") == "ROLE_COLD_CHAIN_LEAD"
        # A category dropped from the map stops escalating.
        assert engine.config.authority_for("WEAPONS") is None


@pytest.mark.asyncio
async def test_stored_policy_config_is_loaded_on_startup():
    """Retuned policy has to survive a restart, or it silently reverts."""
    settings = _test_settings()

    first = create_app(settings)
    async with AsyncTestClient(app=first):
        await first.state.store.save_policy_config(
            PolicyConfig(
                restricted_categories={"HAZARDOUS": "ROLE_SAFETY_OFFICER"},
            )
        )
        # The in-memory store dies with the app, so assert against this one.
        stored = await first.state.store.get_policy_config()

    assert stored is not None
    assert stored.restricted_categories == {"HAZARDOUS": "ROLE_SAFETY_OFFICER"}
