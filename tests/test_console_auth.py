"""Console sign-in, over HTTP and at the service boundary.

The console is unreachable without these endpoints, so what is pinned here is
not only the happy path but the ways a sign-in must fail: a wrong password, a
missing actor, a revoked session, and a cookie that outlived its session.
"""

from datetime import datetime, timedelta, timezone

import pytest
from litestar.testing import AsyncTestClient

from zonegate.app import DEMO_AUTHORITIES, create_app
from zonegate.authorization.console import (
    SESSION_COOKIE,
    ConsoleAuthService,
    InvalidCredentialsError,
    WeakPasswordError,
)
from zonegate.authorization.passwords import hash_password, verify_password
from zonegate.config import Settings
from zonegate.domain.actors import Actor, DeviceBinding
from zonegate.domain.credentials import ConsoleSession


def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        ZOVA_DB_PATH=":memory:",
        NOKIA_MCP_ENABLED=False,
        LLM_PROVIDER="ollama",
        OLLAMA_BASE_URL="http://127.0.0.1:59999",
        DEMO_OPERATOR_PASSWORD="",
    )


def operator(actor_id: str = "usr_console_01", status: str = "ACTIVE") -> Actor:
    return Actor(
        actor_id=actor_id,
        role="ROLE_CARGO_SUPERVISOR",
        permissions=["cargo:release"],
        registered_phone_number="+14155550199",
        registered_device_id="dev_imei_99887766",
        enrollment_status=status,
    )


async def enrol(store, actor: Actor, password: str | None = "correct-horse") -> None:
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


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_a_hash_never_contains_the_password():
    stored = hash_password("correct-horse")

    assert "correct-horse" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_the_same_password_hashes_differently_every_time():
    """A shared salt would let one cracked hash unlock every reuse of it."""
    assert hash_password("correct-horse") != hash_password("correct-horse")


def test_verify_accepts_the_password_and_rejects_everything_else():
    stored = hash_password("correct-horse")

    assert verify_password("correct-horse", stored) is True
    assert verify_password("correct-horsE", stored) is False
    assert verify_password("", stored) is False


@pytest.mark.parametrize(
    "stored",
    ["", "nonsense", "bcrypt$1$aa$bb", "pbkdf2_sha256$notanumber$aa$bb", "pbkdf2_sha256$1$zz$bb"],
)
def test_a_malformed_stored_hash_fails_the_sign_in_rather_than_raising(stored):
    """Tampered storage must not be able to crash the auth path."""
    assert verify_password("correct-horse", stored) is False


def test_an_empty_password_cannot_be_hashed():
    with pytest.raises(ValueError):
        hash_password("")


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_in_returns_the_actor_and_opens_a_session(memory_store):
    await enrol(memory_store, operator())
    service = ConsoleAuthService(memory_store)

    actor, session = await service.sign_in("usr_console_01", "correct-horse")

    assert actor.actor_id == "usr_console_01"
    assert session.expires_at > session.created_at
    assert (await service.resolve(session.session_id)).actor_id == "usr_console_01"


@pytest.mark.asyncio
async def test_a_wrong_password_and_an_unknown_actor_fail_identically(memory_store):
    """Different messages would turn the sign-in form into a roster listing."""
    await enrol(memory_store, operator())
    service = ConsoleAuthService(memory_store)

    with pytest.raises(InvalidCredentialsError) as wrong:
        await service.sign_in("usr_console_01", "wrong")

    with pytest.raises(InvalidCredentialsError) as unknown:
        await service.sign_in("usr_nobody", "correct-horse")

    assert str(wrong.value) == str(unknown.value)


@pytest.mark.asyncio
async def test_an_actor_with_no_password_cannot_sign_in(memory_store):
    """A field operator enrolled for the mobile app is not a console user."""
    await enrol(memory_store, operator("usr_field_01"), password=None)

    with pytest.raises(InvalidCredentialsError):
        await ConsoleAuthService(memory_store).sign_in("usr_field_01", "anything")


@pytest.mark.asyncio
async def test_a_suspended_actor_cannot_sign_in_even_with_the_right_password(memory_store):
    await enrol(memory_store, operator("usr_suspended", status="SUSPENDED"))

    with pytest.raises(InvalidCredentialsError):
        await ConsoleAuthService(memory_store).sign_in("usr_suspended", "correct-horse")


@pytest.mark.asyncio
async def test_signing_out_revokes_the_session_not_only_the_cookie(memory_store):
    await enrol(memory_store, operator())
    service = ConsoleAuthService(memory_store)
    _, session = await service.sign_in("usr_console_01", "correct-horse")

    await service.sign_out(session.session_id)

    assert await service.resolve(session.session_id) is None


@pytest.mark.asyncio
async def test_an_expired_session_resolves_to_nobody_and_is_deleted(memory_store):
    await enrol(memory_store, operator())
    service = ConsoleAuthService(memory_store)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await memory_store.save_console_session(
        ConsoleSession(
            session_id="stale",
            actor_id="usr_console_01",
            created_at=past - timedelta(hours=12),
            expires_at=past,
        )
    )

    assert await service.resolve("stale") is None
    assert await memory_store.get_console_session("stale") is None


@pytest.mark.asyncio
async def test_resolving_an_unknown_or_absent_cookie_is_not_an_error(memory_store):
    service = ConsoleAuthService(memory_store)

    assert await service.resolve(None) is None
    assert await service.resolve("") is None
    assert await service.resolve("never-issued") is None


@pytest.mark.asyncio
async def test_a_short_password_is_refused(memory_store):
    with pytest.raises(WeakPasswordError):
        await ConsoleAuthService(memory_store).set_password("usr_console_01", "short")


@pytest.mark.asyncio
async def test_changing_a_password_invalidates_nothing_but_the_old_password(memory_store):
    await enrol(memory_store, operator())
    service = ConsoleAuthService(memory_store)

    await service.set_password("usr_console_01", "a-brand-new-one")

    with pytest.raises(InvalidCredentialsError):
        await service.sign_in("usr_console_01", "correct-horse")

    actor, _ = await service.sign_in("usr_console_01", "a-brand-new-one")
    assert actor.actor_id == "usr_console_01"


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_session_endpoint_is_401_until_someone_signs_in():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store, operator())

        assert (await client.get("/v1/auth/session")).status_code == 401

        signed_in = await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_console_01", "password": "correct-horse"},
        )
        assert signed_in.status_code == 200
        assert signed_in.json()["actor"]["actor_id"] == "usr_console_01"

        session = await client.get("/v1/auth/session")
        assert session.status_code == 200
        assert session.json()["actor"]["role"] == "ROLE_CARGO_SUPERVISOR"


@pytest.mark.asyncio
async def test_the_session_cookie_is_http_only_so_no_page_script_can_read_it():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store, operator())

        response = await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_console_01", "password": "correct-horse"},
        )

        raw = response.headers["set-cookie"]
        assert SESSION_COOKIE in raw
        assert "httponly" in raw.lower()
        assert "samesite=lax" in raw.lower()


@pytest.mark.asyncio
async def test_a_rejected_sign_in_sets_no_cookie():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store, operator())

        response = await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_console_01", "password": "wrong"},
        )

        assert response.status_code == 401
        assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_logging_out_ends_the_session_over_http():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store, operator())
        await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_console_01", "password": "correct-horse"},
        )

        assert (await client.post("/v1/auth/logout")).status_code == 204
        assert (await client.get("/v1/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_a_password_change_needs_a_session_and_then_takes_effect():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store, operator())

        assert (
            await client.post("/v1/auth/password", json={"password": "no-session-here"})
        ).status_code == 401

        await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_console_01", "password": "correct-horse"},
        )
        assert (
            await client.post("/v1/auth/password", json={"password": "a-brand-new-one"})
        ).status_code == 204

        await client.post("/v1/auth/logout")
        assert (
            await client.post(
                "/v1/auth/login",
                json={"actor_id": "usr_console_01", "password": "correct-horse"},
            )
        ).status_code == 401
        assert (
            await client.post(
                "/v1/auth/login",
                json={"actor_id": "usr_console_01", "password": "a-brand-new-one"},
            )
        ).status_code == 200


@pytest.mark.asyncio
async def test_a_short_password_is_refused_over_http():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        await enrol(app.state.store, operator())
        await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_console_01", "password": "correct-horse"},
        )

        assert (
            await client.post("/v1/auth/password", json={"password": "short"})
        ).status_code == 400


@pytest.mark.asyncio
async def test_every_escalation_authority_is_seeded_with_a_usable_console_password():
    """A fresh database with nobody who can settle a hold makes the console a dead end."""
    app = create_app(
        Settings(
            APP_ENV="development",
            ZOVA_DB_PATH=":memory:",
            NOKIA_MCP_ENABLED=False,
            LLM_PROVIDER="ollama",
            OLLAMA_BASE_URL="http://127.0.0.1:59999",
            DEMO_OPERATOR_PASSWORD="zonegate-demo",
        )
    )

    async with AsyncTestClient(app=app) as client:
        for actor_id, role in DEMO_AUTHORITIES:
            response = await client.post(
                "/v1/auth/login",
                json={"actor_id": actor_id, "password": "zonegate-demo"},
            )
            assert response.status_code == 200, actor_id
            assert response.json()["actor"]["role"] == role


@pytest.mark.asyncio
async def test_the_seeded_cargo_operator_gets_no_console_access():
    """The field operator requests from the app; the console is not theirs."""
    app = create_app(
        Settings(
            APP_ENV="development",
            ZOVA_DB_PATH=":memory:",
            NOKIA_MCP_ENABLED=False,
            LLM_PROVIDER="ollama",
            OLLAMA_BASE_URL="http://127.0.0.1:59999",
            DEMO_OPERATOR_PASSWORD="zonegate-demo",
        )
    )

    async with AsyncTestClient(app=app) as client:
        assert await app.state.store.get_actor("usr_cargo_operator_01") is not None
        response = await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_cargo_operator_01", "password": "zonegate-demo"},
        )

        assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_database_that_predates_the_authority_accounts_still_gets_them(tmp_path):
    """A deployment that already has a supervisor on file, but no password for them.

    Two startups against one file: the first stands in for an older
    deployment -- seeding off, the supervisor enrolled by hand without a
    credential. The second must notice and give that account its password.
    """
    db = str(tmp_path / "upgrade.zova")

    def at(password: str) -> Settings:
        return Settings(
            APP_ENV="development",
            ZOVA_DB_PATH=db,
            NOKIA_MCP_ENABLED=False,
            LLM_PROVIDER="ollama",
            OLLAMA_BASE_URL="http://127.0.0.1:59999",
            DEMO_OPERATOR_PASSWORD=password,
        )

    before = create_app(at(""))
    async with AsyncTestClient(app=before):
        await before.state.store.save_actor(
            Actor(
                actor_id="usr_cargo_supervisor_01",
                role="ROLE_CARGO_SUPERVISOR",
                permissions=["hold:resolve"],
                registered_phone_number="+99999991002",
                registered_device_id="console_workstation",
                enrollment_status="ACTIVE",
            )
        )
        assert await before.state.store.get_credential("usr_cargo_supervisor_01") is None

    after = create_app(at("zonegate-demo"))
    async with AsyncTestClient(app=after) as client:
        response = await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_cargo_supervisor_01", "password": "zonegate-demo"},
        )

        assert response.status_code == 200


@pytest.mark.asyncio
async def test_seeding_never_overwrites_a_password_somebody_already_set(tmp_path):
    """A restart must not silently reset a demo authority to the default."""
    db = str(tmp_path / "kept.zova")

    def at() -> Settings:
        return Settings(
            APP_ENV="development",
            ZOVA_DB_PATH=db,
            NOKIA_MCP_ENABLED=False,
            LLM_PROVIDER="ollama",
            OLLAMA_BASE_URL="http://127.0.0.1:59999",
            DEMO_OPERATOR_PASSWORD="zonegate-demo",
        )

    first = create_app(at())
    async with AsyncTestClient(app=first) as client:
        await client.post(
            "/v1/auth/login",
            json={"actor_id": "usr_cargo_supervisor_01", "password": "zonegate-demo"},
        )
        await client.post("/v1/auth/password", json={"password": "chosen-by-a-person"})

    second = create_app(at())
    async with AsyncTestClient(app=second) as client:
        assert (
            await client.post(
                "/v1/auth/login",
                json={"actor_id": "usr_cargo_supervisor_01", "password": "zonegate-demo"},
            )
        ).status_code == 401
        assert (
            await client.post(
                "/v1/auth/login",
                json={"actor_id": "usr_cargo_supervisor_01", "password": "chosen-by-a-person"},
            )
        ).status_code == 200
