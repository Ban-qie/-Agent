import pytest

from data_formulator.datalake.workspace_manager import WorkspaceManager
from data_formulator.ecommerce.v1_workspace import V1WorkspaceStore
from data_formulator.ecommerce.contracts import ToolError


def _conditions():
    return {"operation": "summarize", "metrics": ["sales_amount"],
            "current": {"start": "2018-01-01", "end": "2018-02-01"},
            "baseline": None, "regions": [], "group_by": "region"}


def test_v1_nodes_round_trip_and_branch_parent_conditions(tmp_path):
    store = V1WorkspaceStore(WorkspaceManager(tmp_path), "local:test")
    root = store.save_run(node_id="v1-root-001", question="root", conditions=_conditions(),
                          status="success", result={"state": "success"})
    child = store.save_run(node_id="v1-child-001", parent_node_id="v1-root-001", question="child",
                           conditions={**_conditions(), "group_by": "day"}, status="success")
    assert store.read()["nodes"][-1]["parent_node_id"] == root["node_id"]
    assert store.parent_conditions(child["node_id"])["group_by"] == "day"


def test_v1_rejects_unknown_parent_and_conflicting_replay(tmp_path):
    store = V1WorkspaceStore(WorkspaceManager(tmp_path), "local:test")
    with pytest.raises(ToolError) as exc:
        store.save_run(node_id="v1-child-002", parent_node_id="v1-missing", question="x",
                       conditions=_conditions(), status="success")
    assert exc.value.code == "PARENT_NOT_FOUND"
    store.save_run(node_id="v1-root-002", question="root", conditions=_conditions(), status="success")
    with pytest.raises(ToolError) as exc:
        store.save_run(node_id="v1-root-002", question="changed", conditions=_conditions(), status="success")
    assert exc.value.code == "REQUEST_CONFLICT"


def test_v1_running_node_is_interrupted_after_reload(tmp_path):
    store = V1WorkspaceStore(WorkspaceManager(tmp_path), "local:test")
    store.save_run(node_id="v1-run-001", question="running", conditions=_conditions(), status="running")
    restored = store.read()
    assert restored["nodes"][0]["status"] == "interrupted"
    assert restored["nodes"][0]["error"]["code"] == "INTERRUPTED"
