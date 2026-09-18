"""LangGraph skeleton for the V1 ecommerce path.

The graph is intentionally a wiring layer at this stage.  Real condition
normalization, query generation, validation, and execution handlers are
injected by later V1 stages.  The default nodes only record traversal and
never claim a business result.
"""
from __future__ import annotations

from typing import Annotated, Any, Callable, Mapping, TypedDict
import operator

from langgraph.graph import END, START, StateGraph

from data_formulator.ecommerce.v1_contracts import (
    V1_STATUSES,
    V1_TERMINAL_STATUSES,
    V1GraphState,
)


class GraphState(TypedDict, total=False):
    state_version: int
    run_id: str
    workspace_id: str
    snapshot_id: str
    metric_version: str
    node_id: str
    parent_node_id: str | None
    user_question: str
    normalized_conditions: dict[str, Any] | None
    plan: dict[str, Any] | None
    selected_sources: list[str]
    query: dict[str, Any] | None
    verified_result: dict[str, Any] | None
    chart_spec: dict[str, Any] | None
    explanation: dict[str, Any] | None
    status: str
    error: dict[str, Any] | None
    budget: dict[str, Any]
    trace: Annotated[list[dict[str, Any]], operator.add]
    graph_stage: str


Handler = Callable[[dict[str, Any]], Mapping[str, Any]]
_STAGES = (
    "planner",
    "source_selector",
    "query_generator",
    "query_validator",
    "executor",
    "interpreter",
    "chart_planner",
)
_STATE_KEYS = set(GraphState.__annotations__) - {"trace"}


def _record(stage: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "graph_stage": stage,
        "trace": [{"stage": stage, "status": state.get("status", "running")}],
    }


def _invoke(stage: str, state: dict[str, Any], handlers: Mapping[str, Handler]) -> dict[str, Any]:
    if state.get("status") in V1_TERMINAL_STATUSES or state.get("status") == "waiting_clarification":
        return _record(stage, state)
    updates = dict(handlers[stage](dict(state))) if stage in handlers else {}
    unknown = set(updates) - _STATE_KEYS
    if unknown:
        raise ValueError(f"{stage} returned unknown state fields: {sorted(unknown)}")
    if "status" in updates and updates["status"] not in V1_STATUSES:
        raise ValueError(f"{stage} returned unknown status: {updates['status']}")
    next_state = {**state, **updates}
    return {**updates, **_record(stage, next_state)}


def v1_default_handlers() -> dict[str, Handler]:
    """Return the deterministic V1 planner handler.

    The remaining handlers stay injectable until their business tools are
    migrated.  Keeping this factory separate preserves the old no-handler
    traversal fixture and makes the planner contract directly testable.
    """
    from data_formulator.ecommerce.v1_normalization import planner_handler

    return {"planner": planner_handler}


def _route_after_planner(state: GraphState) -> str:
    if state.get("status") == "waiting_clarification":
        return "end"
    return "source_selector"


def _route_after_validator(state: GraphState) -> str:
    if state.get("status") in {"failed", "waiting_clarification"}:
        return "end"
    return "executor"


def _route_after_executor(state: GraphState) -> str:
    if state.get("status") in {"failed", "interrupted", "waiting_clarification"}:
        return "end"
    return "interpreter"


def build_v1_graph(handlers: Mapping[str, Handler] | None = None):
    """Compile one top-level graph with injectable node handlers.

    ``handlers`` is used by later stages and tests.  Omitting it enables the
    deterministic V1 planner; passing an empty mapping explicitly retains a
    side-effect-free traversal graph for routing and persistence checks.
    """
    node_handlers = dict(v1_default_handlers() if handlers is None else handlers)
    unknown = set(node_handlers) - set(_STAGES)
    if unknown:
        raise ValueError(f"unknown V1 graph handlers: {sorted(unknown)}")

    graph = StateGraph(GraphState)
    for stage in _STAGES:
        graph.add_node(stage, lambda state, stage=stage: _invoke(stage, state, node_handlers))
    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", _route_after_planner,
                                {"source_selector": "source_selector", "end": END})
    graph.add_edge("source_selector", "query_generator")
    graph.add_edge("query_generator", "query_validator")
    graph.add_conditional_edges("query_validator", _route_after_validator,
                                {"executor": "executor", "end": END})
    graph.add_conditional_edges("executor", _route_after_executor,
                                {"interpreter": "interpreter", "end": END})
    graph.add_edge("interpreter", "chart_planner")
    graph.add_edge("chart_planner", END)
    return graph.compile()


def invoke_v1_graph(initial_state: Mapping[str, Any], handlers: Mapping[str, Handler] | None = None,
                    config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate the public state contract, then invoke the compiled graph."""
    state = V1GraphState.from_dict(initial_state)
    output = build_v1_graph(handlers).invoke(state.to_dict(), config=config)
    # LangGraph combines the trace reducer; normalize the list representation
    # before validating the state returned to callers.
    output["selected_sources"] = tuple(output.get("selected_sources", ()))
    contract_fields = set(V1GraphState.__dataclass_fields__)
    V1GraphState.from_dict({key: value for key, value in output.items() if key in contract_fields})
    return output
