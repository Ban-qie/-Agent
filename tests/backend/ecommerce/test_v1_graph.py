from data_formulator.ecommerce.orchestrator import resolve_analysis_entrypoint, selected_mode
from data_formulator.ecommerce.v1_graph import invoke_v1_graph
from data_formulator.ecommerce.v1_contracts import ContractError


SNAPSHOT = "b" * 64


def _state():
    return {
        "run_id": "run_123456",
        "workspace_id": "ecommerce-v0",
        "snapshot_id": SNAPSHOT,
        "metric_version": "v1",
        "node_id": "node_123456",
        "user_question": "比较两个期间",
        "trace": [],
    }


def test_default_graph_traverses_one_top_level_path_without_business_result():
    result = invoke_v1_graph(_state(), handlers={})
    assert [item["stage"] for item in result["trace"]] == [
        "planner", "source_selector", "query_generator", "query_validator",
        "executor", "interpreter", "chart_planner",
    ]
    assert result["status"] == "running"
    assert result["verified_result"] is None


def test_graph_stops_before_tools_when_planner_requires_clarification():
    result = invoke_v1_graph(
        _state(),
        handlers={"planner": lambda state: {"status": "waiting_clarification"}},
    )
    assert result["status"] == "waiting_clarification"
    assert [item["stage"] for item in result["trace"]] == ["planner"]


def test_v0_is_the_default_and_v1_is_explicit():
    assert selected_mode(None) == "v0"
    assert resolve_analysis_entrypoint() == "run_analysis"
    assert selected_mode("v1") == "v1"
    assert resolve_analysis_entrypoint("v1") == "invoke_v1_graph"
