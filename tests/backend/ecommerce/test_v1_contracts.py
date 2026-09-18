from data_formulator.ecommerce.v1_contracts import (
    ContractError,
    V1GraphState,
    V1_HANDOFFS,
    V1_ROLE_SPECS,
    validate_status_transition,
    validate_v1_contracts,
)


SNAPSHOT = "a" * 64


def test_v1_static_roles_and_handoffs_are_valid():
    validate_v1_contracts()
    assert {role.name for role in V1_ROLE_SPECS} >= {
        "planner", "source_selector", "query_generator", "query_validator",
        "executor", "interpreter", "chart_planner",
    }
    assert V1_HANDOFFS[0].source == "planner"


def test_graph_state_round_trips_with_tuple_sources():
    state = V1GraphState(
        run_id="run_123456",
        workspace_id="ecommerce-v0",
        snapshot_id=SNAPSHOT,
        metric_version="v1",
        node_id="node_123456",
        user_question="比较两个期间的销售额",
        normalized_conditions={"group_by": "region"},
        selected_sources=("orders",),
    )
    restored = V1GraphState.from_dict(state.to_dict())
    assert restored == state


def test_graph_state_rejects_invalid_terminal_status_and_id():
    state = V1GraphState(
        run_id="run_123456",
        workspace_id="ecommerce-v0",
        snapshot_id=SNAPSHOT,
        metric_version="v1",
        node_id="node_123456",
        user_question="问题",
    )
    state.status = "unknown"
    try:
        state.validate()
    except ContractError:
        pass
    else:
        raise AssertionError("invalid status was accepted")


def test_status_transitions_keep_terminal_results_closed():
    validate_status_transition("running", "waiting_clarification")
    validate_status_transition("waiting_clarification", "running")
    validate_status_transition("interrupted", "running")
    try:
        validate_status_transition("success", "running")
    except ContractError:
        pass
    else:
        raise AssertionError("terminal result was reopened")
