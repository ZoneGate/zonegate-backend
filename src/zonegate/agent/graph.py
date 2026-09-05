from langgraph.graph import StateGraph, START, END
from zonegate.agent.context_evaluator import ContextEvaluator
from zonegate.agent.evidence_planner import EvidencePlanner
from zonegate.agent.state import AgentState


def build_evidence_planning_graph(planner: EvidencePlanner):
    """Small, focused LangGraph graph for Stage 1: AI Evidence Planning."""
    async def plan_node(state: AgentState) -> dict:
        transaction = state["transaction"]
        workflow_name = state.get("workflow_name", "RELEASE_CARGO")
        allowed = state.get("allowed_optional", [])
        try:
            plan = await planner.plan(transaction, workflow_name, allowed)
            return {"evidence_plan": plan}
        except Exception as exc:
            return {"error": str(exc), "evidence_plan": None}

    builder = StateGraph(AgentState)
    builder.add_node("plan_evidence", plan_node)
    builder.add_edge(START, "plan_evidence")
    builder.add_edge("plan_evidence", END)
    return builder.compile()


def build_context_evaluation_graph(evaluator: ContextEvaluator):
    """Small, focused LangGraph graph for Stage 2: AI Context Evaluation."""
    async def evaluate_node(state: AgentState) -> dict:
        transaction = state["transaction"]
        evidence = state.get("canonical_evidence")
        if evidence is None:
            return {"error": "Cannot evaluate context without canonical evidence", "context_evaluation": None}
        try:
            evaluation = await evaluator.evaluate(transaction, evidence)
            return {"context_evaluation": evaluation}
        except Exception as exc:
            return {"error": str(exc), "context_evaluation": None}

    builder = StateGraph(AgentState)
    builder.add_node("evaluate_context", evaluate_node)
    builder.add_edge(START, "evaluate_context")
    builder.add_edge("evaluate_context", END)
    return builder.compile()
