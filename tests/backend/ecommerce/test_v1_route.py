from pathlib import Path

from data_formulator.datalake.workspace_manager import WorkspaceManager
from data_formulator.ecommerce.v1_workspace import V1WorkspaceStore


def test_v1_workspace_route_returns_parent_nodes(monkeypatch, tmp_path):
    from devtools.run_local import configure_offline
    configure_offline()
    from data_formulator.app import app
    from data_formulator.auth import identity
    from data_formulator.routes import ecommerce as routes

    store = V1WorkspaceStore(WorkspaceManager(tmp_path / "workspaces"), "local:v1-route")
    store.save_run(node_id="v1-route-001", question="root", conditions={"x": 1}, status="success",
                   result={"state": "success"})
    monkeypatch.setattr(identity, "_localhost_identity", "local:v1-route")
    monkeypatch.setattr(identity, "_provider", None)
    monkeypatch.setattr(routes, "_v1_workspace", lambda _: store)
    monkeypatch.setenv("ECOMMERCE_RESTRICTED", "true")
    monkeypatch.setenv("ECOMMERCE_ANALYSIS_ORCHESTRATOR", "v1")
    response = app.test_client().get("/api/ecommerce/workspace")
    assert response.status_code == 200
    assert response.json["nodes"][0]["node_id"] == "v1-route-001"


def test_v1_service_replays_completed_node_without_running_graph(monkeypatch, tmp_path):
    from data_formulator.ecommerce import v1_service
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / "workspaces"), "local:test")
    saved = {"state": "success", "result": {"state": "success"}}
    store.save_run(node_id="v1-replay-001", question="分析2018年1月销售额", conditions={},
                   status="success", result=saved)
    monkeypatch.setattr(v1_service, "invoke_v1_business_graph", lambda *args: (_ for _ in ()).throw(AssertionError("rerun")))
    assert v1_service.analyze_v1({"request_id": "v1-replay-001", "user_question": "分析2018年1月销售额"},
                                 "local:test", Path(tmp_path / "audit"), store) == saved
