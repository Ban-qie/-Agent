import pytest


@pytest.fixture
def client(monkeypatch):
    from devtools.run_local import configure_offline
    configure_offline()
    from data_formulator.app import app
    from data_formulator.auth import identity
    monkeypatch.setattr(identity, "_localhost_identity", "local:test-profile")
    monkeypatch.setattr(identity, "_provider", None)
    return app.test_client()


@pytest.mark.parametrize("route", ["/api/agent/analyst-streaming", "/api/agent/data-loading-chat",
                                    "/api/agent/refresh-derived-data", "/api/tables/analyze",
                                    "/api/agent/test-model", "/api/agent/check-available-models",
                                    "/api/agent/workspace-name", "/api/agent/derive-starter-questions",
                                    "/api/tables/parse-file", "/api/tables/upload-db-file",
                                    "/api/model-endpoints", "/api/unknown-future-route"])
def test_legacy_execution_and_implicit_model_routes_blocked(client, route):
    response = client.post(route, json={"code": "raise RuntimeError('must not execute')", "is_global": True})
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "TOOL_NOT_ALLOWED"


def test_safe_catalog_and_http_parameter_guards(client):
    assert client.get("/api/app-config").status_code == 200
    assert client.get("/api/ecommerce/catalog").status_code == 200
    assert client.get("/api/ecommerce/catalog", headers={"Origin": "https://external.example"}).status_code == 403
    assert client.post("/api/ecommerce/query", json={"sql": "SELECT 1"}).status_code == 400
    assert client.post("/api/ecommerce/query", data=b"x" * 8193, content_type="application/json").status_code == 413
    assert client.get("/api/ecommerce/catalog", environ_base={"REMOTE_ADDR": "192.0.2.1"}).status_code == 403


def test_all_unlisted_actual_api_routes_deny_before_handler(client):
    from data_formulator.app import app
    allowed = {"/api/app-config", "/api/auth/info", "/api/agent/list-global-models",
               "/api/ecommerce/catalog", "/api/ecommerce/query", "/api/ecommerce/analyze",
               "/api/ecommerce/workspace"}
    checked = 0
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/") or rule.rule in allowed:
            continue
        # Variable routes are also denied by the path-level default deny guard.
        import re
        path = re.sub(r"<[^>]+>", "test", rule.rule)
        method = "POST" if "POST" in rule.methods else "GET"
        response = client.open(path, method=method, json={})
        assert response.status_code == 403, (path, response.status_code)
        checked += 1
    assert checked > 40


def test_direct_exploration_visualization_session_and_sql_guard(monkeypatch):
    monkeypatch.setenv("ECOMMERCE_RESTRICTED", "true")
    from data_formulator.analyst.agent import AnalystAgent
    from data_formulator.sandbox import create_sandbox
    from data_formulator.sandbox.local_sandbox import SandboxSession, LocalSandbox
    from data_formulator.sandbox.docker_sandbox import DockerSandbox
    from data_formulator.datalake.workspace import Workspace
    from data_formulator.datalake.azure_blob_workspace import AzureBlobWorkspace
    agent = object.__new__(AnalystAgent)
    with pytest.raises(PermissionError):
        agent._run_explore_code("print(1)", [])
    with pytest.raises(PermissionError):
        agent._run_visualize_code("print(1)", "out", {}, {}, {}, "test")
    with pytest.raises(PermissionError):
        SandboxSession()
    with pytest.raises(PermissionError):
        create_sandbox("docker")
    with pytest.raises(PermissionError):
        LocalSandbox._run_in_warm_subprocess("print(1)", {})
    with pytest.raises(PermissionError):
        LocalSandbox().run_python_code("print(1)", None, "out")
    with pytest.raises(PermissionError):
        DockerSandbox().run_python_code("print(1)", None, "out")
    with pytest.raises(PermissionError):
        Workspace.run_parquet_sql(None, "orders", "SELECT * FROM {parquet}")
    with pytest.raises(PermissionError):
        AzureBlobWorkspace.run_parquet_sql(None, "orders", "SELECT * FROM {parquet}")
