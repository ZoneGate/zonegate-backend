"""Tests for the LangGraph layer that wraps the two advisory agent stages.

Both graphs sit on the authorization path, and both are allowed to fail. What
must never happen is a failure that propagates as an exception -- the pipeline
treats a missing plan as "fall back to the mandatory baseline" and a missing
evaluation as "recommend HOLD", and it can only do that if the graph returns
instead of raising.

So the behaviour under test is mostly the unhappy path: the graph swallows the
error, records it in state, and hands back a None the caller can recognise.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from zonegate.agent.graph import (
    build_context_evaluation_graph,
    build_evidence_planning_graph,
)
from zonegate.domain.decisions import ContextEvaluation, RecommendedControl
from zonegate.domain.evidence import CanonicalEvidence, EvidenceKind, EvidencePlan
from zonegate.domain.transactions import TransactionRequest


def transaction(value: str = "1500.00") -> TransactionRequest:
    return TransactionRequest(
        transaction_id="tx_graph_1",
        actor_id="usr_cargo_operator_01",
        action="RELEASE_CARGO",
        resource_id="CT-1",
        zone="PORT_GATE_17",
        timestamp=datetime(2026, 9, 10, 11, 0, tzinfo=UTC),
        value=Decimal(value),
    )


def evidence(**fields) -> CanonicalEvidence:
    """Canonical evidence always carries the moment it was collected."""
    fields.setdefault("collected_at", datetime(2026, 9, 10, 11, 0, tzinfo=UTC))
    return CanonicalEvidence(**fields)


class StubPlanner:
    """Stands in for the EvidencePlanner, recording what the graph passed it."""

    def __init__(self, plan: EvidencePlan | None = None, raises: Exception | None = None):
        self._plan = plan
        self._raises = raises
        self.calls: list[tuple] = []

    async def plan(self, transaction, workflow_name, allowed_optional):
        self.calls.append((transaction, workflow_name, list(allowed_optional)))
        if self._raises:
            raise self._raises
        return self._plan


class StubEvaluator:
    """Stands in for the ContextEvaluator."""

    def __init__(
        self,
        evaluation: ContextEvaluation | None = None,
        raises: Exception | None = None,
    ):
        self._evaluation = evaluation
        self._raises = raises
        self.calls: list[tuple] = []

    async def evaluate(self, transaction, evidence):
        self.calls.append((transaction, evidence))
        if self._raises:
            raise self._raises
        return self._evaluation


# --- Stage 1: evidence planning ------------------------------------------


@pytest.mark.asyncio
async def test_planning_graph_returns_the_plan_the_planner_produced():
    plan = EvidencePlan(
        optional_evidence=[EvidenceKind.SIM_SWAP],
        rationale=["high value outside the operating window"],
    )
    graph = build_evidence_planning_graph(StubPlanner(plan=plan))

    state = await graph.ainvoke(
        {
            "transaction": transaction(),
            "workflow_name": "RELEASE_CARGO",
            "allowed_optional": [EvidenceKind.SIM_SWAP, EvidenceKind.DEVICE_SWAP],
        }
    )

    assert state["evidence_plan"].optional_evidence == [EvidenceKind.SIM_SWAP]
    assert not state.get("error")


@pytest.mark.asyncio
async def test_planning_graph_forwards_only_the_permitted_optional_kinds():
    # The allow-list is decided by workflow policy, not by the agent. If the
    # graph did not pass it through, the planner would choose from nothing.
    planner = StubPlanner(plan=EvidencePlan())
    graph = build_evidence_planning_graph(planner)

    await graph.ainvoke(
        {
            "transaction": transaction(),
            "workflow_name": "RELEASE_CARGO",
            "allowed_optional": [EvidenceKind.REACHABILITY],
        }
    )

    _, workflow_name, allowed = planner.calls[0]
    assert workflow_name == "RELEASE_CARGO"
    assert allowed == [EvidenceKind.REACHABILITY]


@pytest.mark.asyncio
async def test_a_planner_failure_is_returned_as_state_not_raised():
    # The pipeline falls back to the mandatory baseline on a missing plan. That
    # fallback is unreachable if the graph raises instead of returning.
    graph = build_evidence_planning_graph(
        StubPlanner(raises=RuntimeError("Ollama timed out after 120s"))
    )

    state = await graph.ainvoke(
        {
            "transaction": transaction(),
            "workflow_name": "RELEASE_CARGO",
            "allowed_optional": [],
        }
    )

    assert state["evidence_plan"] is None
    assert "timed out" in state["error"]


@pytest.mark.asyncio
async def test_the_planning_graph_defaults_the_workflow_when_none_was_given():
    planner = StubPlanner(plan=EvidencePlan())
    graph = build_evidence_planning_graph(planner)

    await graph.ainvoke({"transaction": transaction()})

    assert planner.calls[0][1] == "RELEASE_CARGO"


# --- Stage 2: context evaluation -----------------------------------------


@pytest.mark.asyncio
async def test_evaluation_graph_returns_the_assessment():
    evaluation = ContextEvaluation(
        risk_factors=["number verified"],
        recommended_control=RecommendedControl.APPROVE,
        rationale="nothing anomalous",
    )
    graph = build_context_evaluation_graph(StubEvaluator(evaluation=evaluation))

    state = await graph.ainvoke(
        {
            "transaction": transaction(),
            "canonical_evidence": evidence(number_verified=True, location_verified=True),
        }
    )

    assert state["context_evaluation"].recommended_control == RecommendedControl.APPROVE
    assert not state.get("error")


@pytest.mark.asyncio
async def test_evaluation_refuses_to_run_without_evidence():
    # Assessing risk before the carrier has answered would be an opinion formed
    # on nothing, and it sits one step before the deterministic engine.
    evaluator = StubEvaluator(
        evaluation=ContextEvaluation(
            recommended_control=RecommendedControl.APPROVE,
            rationale="should never run",
        )
    )
    graph = build_context_evaluation_graph(evaluator)

    state = await graph.ainvoke({"transaction": transaction()})

    assert state["context_evaluation"] is None
    assert "without canonical evidence" in state["error"]
    assert evaluator.calls == []


@pytest.mark.asyncio
async def test_an_evaluator_failure_is_returned_as_state_not_raised():
    graph = build_context_evaluation_graph(
        StubEvaluator(raises=RuntimeError("model returned prose"))
    )

    state = await graph.ainvoke(
        {
            "transaction": transaction(),
            "canonical_evidence": evidence(number_verified=True),
        }
    )

    assert state["context_evaluation"] is None
    assert "prose" in state["error"]


@pytest.mark.asyncio
async def test_the_evaluator_sees_the_evidence_that_was_actually_collected():
    evaluator = StubEvaluator(
        evaluation=ContextEvaluation(
            recommended_control=RecommendedControl.HOLD, rationale="sim swap"
        )
    )
    graph = build_context_evaluation_graph(evaluator)
    collected = evidence(
        number_verified=True, location_verified=True, recent_sim_swap=True
    )

    await graph.ainvoke({"transaction": transaction(), "canonical_evidence": collected})

    assert evaluator.calls[0][1].recent_sim_swap is True


@pytest.mark.asyncio
async def test_the_two_graphs_stay_independent():
    # They are invoked at different points in the pipeline, with the carrier
    # calls in between. Neither may require the output of the other to have run.
    planning = build_evidence_planning_graph(StubPlanner(plan=EvidencePlan()))
    evaluation = build_context_evaluation_graph(
        StubEvaluator(
            evaluation=ContextEvaluation(
                recommended_control=RecommendedControl.APPROVE, rationale="ok"
            )
        )
    )

    planned = await planning.ainvoke({"transaction": transaction()})
    assert planned.get("context_evaluation") is None

    evaluated = await evaluation.ainvoke(
        {
            "transaction": transaction(),
            "canonical_evidence": evidence(number_verified=True),
        }
    )
    assert evaluated.get("evidence_plan") is None
