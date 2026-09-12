"""Live pipeline progress.

The mobile app shows these stages while a release is being decided, so what
matters is that an event is emitted where the work actually happens: a stage
that is skipped says so, and the stage that refused a request is the one marked
FAILED. A client that reads them must never be able to disagree with the
decision that follows.
"""

from datetime import datetime, timezone

import pytest
from litestar.testing import AsyncTestClient

from zonegate.app import create_app
from zonegate.authorization.progress import Stage, StageEvent, StageStatus
from zonegate.config import Settings
from zonegate.domain.actors import Actor, DeviceBinding

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


async def enrol(store, permissions: list[str] | None = None) -> None:
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


def payload(transaction_id: str, category: str = "GENERAL", hour: int = 14) -> dict:
    return {
        "transaction_id": transaction_id,
        "actor_id": "usr_cargo_operator_01",
        "action": "RELEASE_CARGO",
        "resource_id": "CT-700",
        "zone": "ZONE_CARGO_BAY_1",
        "timestamp": f"2026-09-10T{hour:02d}:30:00Z",
        "value": "15000.00",
        "category": category,
    }


class Recorder:
    """Collects the events the pipeline emits, in order."""

    def __init__(self) -> None:
        self.events: list[StageEvent] = []

    async def __call__(self, event: StageEvent) -> None:
        self.events.append(event)

    def statuses(self, stage: Stage) -> list[StageStatus]:
        return [event.status for event in self.events if event.stage == stage]

    @property
    def order(self) -> list[Stage]:
        seen: list[Stage] = []
        for event in self.events:
            if event.stage not in seen:
                seen.append(event.stage)
        return seen


# ---------------------------------------------------------------------------
# At the service boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_run_reports_every_stage_in_pipeline_order():
    app = create_app(settings())

    async with AsyncTestClient(app=app):
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)
        recorder = Recorder()

        from zonegate.domain.transactions import TransactionRequest

        decision, _ = await app.state.auth_service.authorize_transaction(
            TransactionRequest.model_validate(payload("tx_progress_clean")),
            listener=recorder,
        )

        assert decision.decision == "APPROVE"
        assert recorder.order == [
            Stage.IDENTITY,
            Stage.PLAN,
            Stage.VALIDATE,
            Stage.EVIDENCE,
            Stage.CONTEXT,
            Stage.POLICY,
        ]
        assert recorder.statuses(Stage.POLICY)[-1] == StageStatus.DONE


@pytest.mark.asyncio
async def test_the_stage_that_refused_the_request_is_the_one_marked_failed():
    app = create_app(settings())

    async with AsyncTestClient(app=app):
        install_fake_nokia(app, FakeNokiaClient())
        recorder = Recorder()

        from zonegate.domain.transactions import TransactionRequest

        # Nobody is enrolled, so the request stops at identity.
        decision, _ = await app.state.auth_service.authorize_transaction(
            TransactionRequest.model_validate(payload("tx_progress_unenrolled")),
            listener=recorder,
        )

        assert decision.decision == "DENY"
        assert recorder.statuses(Stage.IDENTITY) == [
            StageStatus.STARTED,
            StageStatus.FAILED,
        ]
        assert recorder.order == [Stage.IDENTITY]


@pytest.mark.asyncio
async def test_a_carrier_failure_is_reported_against_the_evidence_stage():
    app = create_app(settings())

    async with AsyncTestClient(app=app):
        # No fake carrier installed: the live client cannot reach anything.
        await enrol(app.state.store)
        recorder = Recorder()

        from zonegate.domain.transactions import TransactionRequest

        decision, _ = await app.state.auth_service.authorize_transaction(
            TransactionRequest.model_validate(payload("tx_progress_carrier_down")),
            listener=recorder,
        )

        assert decision.decision == "DENY"
        assert recorder.statuses(Stage.EVIDENCE)[-1] == StageStatus.FAILED
        assert Stage.POLICY not in recorder.order


@pytest.mark.asyncio
async def test_an_unreachable_planner_is_skipped_not_failed():
    """Planning is advisory: losing it must not read as the request failing."""
    app = create_app(settings())

    async with AsyncTestClient(app=app):
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)
        recorder = Recorder()

        from zonegate.domain.transactions import TransactionRequest

        await app.state.auth_service.authorize_transaction(
            TransactionRequest.model_validate(payload("tx_progress_no_agent")),
            listener=recorder,
        )

        assert StageStatus.FAILED not in recorder.statuses(Stage.PLAN)
        assert recorder.statuses(Stage.PLAN)[-1] == StageStatus.SKIPPED


@pytest.mark.asyncio
async def test_every_event_carries_a_readable_label():
    app = create_app(settings())

    async with AsyncTestClient(app=app):
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)
        recorder = Recorder()

        from zonegate.domain.transactions import TransactionRequest

        await app.state.auth_service.authorize_transaction(
            TransactionRequest.model_validate(payload("tx_progress_labels")),
            listener=recorder,
        )

        assert all(event.label.strip() for event in recorder.events)


@pytest.mark.asyncio
async def test_a_listener_is_optional_and_costs_the_plain_path_nothing():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)

        response = await client.post("/v1/authorizations", json=payload("tx_no_listener"))

        assert response.status_code == 201
        assert response.json()["decision"]["decision"] == "APPROVE"


# ---------------------------------------------------------------------------
# Over the wire
# ---------------------------------------------------------------------------


def parse_sse(body: str) -> list[tuple[str, str]]:
    """Reads `event:`/`data:` pairs out of an SSE body."""
    frames: list[tuple[str, str]] = []
    name = ""

    for line in body.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            frames.append((name, line[5:].strip()))

    return frames


@pytest.mark.asyncio
async def test_the_stream_ends_with_the_same_payload_the_plain_endpoint_returns():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)

        response = await client.post("/v1/authorizations/stream", json=payload("tx_stream_ok"))

        assert response.status_code == 200
        frames = parse_sse(response.text)

        assert [name for name, _ in frames].count("result") == 1
        assert frames[-1][0] == "result"

        import json

        result = json.loads(frames[-1][1])
        assert result["decision"]["decision"] == "APPROVE"
        assert result["receipt"]["decision_id"] == result["decision"]["decision_id"]


@pytest.mark.asyncio
async def test_the_stream_reports_the_stages_before_the_result():
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)

        response = await client.post("/v1/authorizations/stream", json=payload("tx_stream_stages"))
        names = [name for name, _ in parse_sse(response.text)]

        assert names.count("stage") >= 6
        assert names.index("result") == len(names) - 1


@pytest.mark.asyncio
async def test_a_streamed_denial_still_ends_with_a_decision():
    """A refused request is a result, not a stream error."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store, permissions=[])

        response = await client.post("/v1/authorizations/stream", json=payload("tx_stream_deny"))
        frames = parse_sse(response.text)

        assert "error" not in [name for name, _ in frames]

        import json

        assert json.loads(frames[-1][1])["decision"]["decision"] == "DENY"


@pytest.mark.asyncio
async def test_the_streamed_decision_is_recorded_like_any_other():
    """A decision made over the stream has to appear in the audit log."""
    app = create_app(settings())

    async with AsyncTestClient(app=app) as client:
        install_fake_nokia(app, FakeNokiaClient())
        await enrol(app.state.store)

        await client.post("/v1/authorizations/stream", json=payload("tx_stream_recorded"))

        listed = (await client.get("/v1/authorizations")).json()

        assert [item["transaction_id"] for item in listed] == ["tx_stream_recorded"]
