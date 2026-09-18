from pathlib import Path
import json
import pytest

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


@pytest.mark.parametrize('question', ['', '  \t', '\ud800', 'a' * 2049, '中' * 683, 42])
def test_v2_invalid_question_before_workspace(question, tmp_path):
    from data_formulator.ecommerce.v1_service import analyze_v1
    from data_formulator.ecommerce.contracts import ToolError
    class Untouched:
        def read(self):
            pytest.fail('invalid structure touched workspace')
    with pytest.raises(ToolError) as exc:
        analyze_v1({'request_id': 'v2-bad-001', 'user_question': question}, 'local:test', tmp_path, Untouched())
    assert exc.value.code == 'INVALID_REQUEST'


@pytest.mark.parametrize('raw,expected', [
    (b'null', 400), (b'[]', 400), (b'{}', 400), (b'not json', 400),
    (b'{"request_id":"v2-bad-001","user_question":"\\ud800"}', 400),
    (b'{"request_id":"v2-bad-001","user_question":" "}', 400),
    (b'{"request_id":"v2-bad-001","user_question":12}', 400),
    (b'{"request_id":"v2-bad-001","user_question":"x","owner":"admin"}', 400),
    (b'{"request_id":"v2-bad-001","user_question":"x","sql":"DROP TABLE orders"}', 400),
    (b'{"request_id":"v2-bad-001","user_question":"x","parent_node_id":"../outside"}', 400),
    (b'[' * 1100 + b'0' + b']' * 1100, 400), (b'x' * 8193, 413),
])
def test_v2_http_rejects_before_workspace(raw, expected, monkeypatch):
    from devtools.run_local import configure_offline
    configure_offline()
    from data_formulator.app import app
    from data_formulator.auth import identity
    from data_formulator.routes import ecommerce as routes
    monkeypatch.setattr(identity, '_localhost_identity', 'local:v2-input')
    monkeypatch.setattr(identity, '_provider', None)
    monkeypatch.setenv('ECOMMERCE_ANALYSIS_ORCHESTRATOR', 'v1')
    touched = []
    def workspace(_):
        touched.append('workspace')
        raise AssertionError('must reject before workspace')
    monkeypatch.setattr(routes, '_v1_workspace', workspace)
    response = app.test_client().post('/api/ecommerce/analyze', data=raw, content_type='application/json')
    assert response.status_code == expected
    assert response.json['error']['code'] in {'INVALID_REQUEST', 'RESOURCE_LIMIT'}
    assert touched == []


@pytest.mark.parametrize('size,declared,expected', [(8192, 8192, 400), (8193, 20, 413), (8193, 8193, 413)])
def test_v2_actual_body_limit(size, declared, expected, monkeypatch):
    from io import BytesIO
    from devtools.run_local import configure_offline
    configure_offline()
    from data_formulator.app import app
    from data_formulator.auth import identity
    monkeypatch.setattr(identity, '_localhost_identity', 'local:v2-bytes')
    monkeypatch.setattr(identity, '_provider', None)
    raw = b'{}' + b' ' * (size - 2)
    response = app.test_client().post('/api/ecommerce/query', environ_overrides={
        'wsgi.input': BytesIO(raw), 'wsgi.input_terminated': True,
        'CONTENT_LENGTH': str(declared), 'CONTENT_TYPE': 'application/json'})
    assert response.status_code == expected


@pytest.mark.parametrize('question', ['a' * 2048, '中' * 682 + 'aa'])
def test_v2_question_byte_boundary(question):
    from data_formulator.ecommerce.v1_service import validate_analyze_request
    validate_analyze_request({'request_id': 'v2-boundary-001', 'user_question': question})


def test_v2_invalid_then_normal_same_client(monkeypatch, tmp_path):
    from devtools.run_local import configure_offline
    configure_offline()
    from data_formulator.app import app
    from data_formulator.auth import identity
    from data_formulator.routes import ecommerce as routes
    from data_formulator.ecommerce import v1_service
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / 'workspaces'), 'local:v2-chain')
    monkeypatch.setattr(identity, '_localhost_identity', 'local:v2-chain')
    monkeypatch.setattr(identity, '_provider', None)
    monkeypatch.setenv('ECOMMERCE_ANALYSIS_ORCHESTRATOR', 'v1')
    monkeypatch.setattr(routes, '_v1_workspace', lambda _: store)
    calls = []
    def graph(*args):
        calls.append('stub')
        return {'status': 'success', 'normalized_conditions': {}, 'verified_result': {'state': 'success'}}
    monkeypatch.setattr(v1_service, 'invoke_v1_business_graph', graph)
    client = app.test_client()
    for raw in [b'null', b'{}', b'not json', b'{"request_id":"v2-chain-001","user_question":"\\ud800"}']:
        assert client.post('/api/ecommerce/analyze', data=raw, content_type='application/json').status_code == 400
    assert calls == []
    assert store.read()['nodes'] == []
    body = {'request_id': 'v2-chain-001', 'user_question': '分析2018年1月销售额'}
    response = client.post('/api/ecommerce/analyze', json=body)
    assert response.status_code == 200 and response.json['state'] == 'success'
    assert calls == ['stub']
    assert [n['status'] for n in store.read()['nodes']] == ['success']
    assert not (tmp_path / 'execution-audit.json').exists()


def test_v2_http_storage_fault_is_sanitized_and_service_recovers(monkeypatch, tmp_path):
    from devtools.run_local import configure_offline
    configure_offline()
    from data_formulator.app import app
    from data_formulator.auth import identity
    from data_formulator.routes import ecommerce as routes
    from data_formulator.ecommerce import v1_service
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / 'workspaces'), 'local:v2-disk')
    monkeypatch.setattr(identity, '_localhost_identity', 'local:v2-disk')
    monkeypatch.setattr(identity, '_provider', None)
    monkeypatch.setenv('ECOMMERCE_ANALYSIS_ORCHESTRATOR', 'v1')
    monkeypatch.setattr(routes, '_v1_workspace', lambda _: store)
    monkeypatch.setattr(v1_service, 'invoke_v1_business_graph', lambda *a: {
        'status': 'success', 'verified_result': {'state': 'success', 'values': {'sales_amount': '30.00'}}})
    save = store.save_run
    def fail(**kwargs):
        if kwargs['status'] != 'running':
            raise OSError('SECRET private path')
        return save(**kwargs)
    monkeypatch.setattr(store, 'save_run', fail)
    client = app.test_client()
    body = {'request_id': 'v2-disk-001', 'user_question': '分析2018年1月销售额'}
    response = client.post('/api/ecommerce/analyze', json=body)
    assert response.status_code == 500 and response.json['state'] == 'failed'
    assert response.json['result']['values']['sales_amount'] == '30.00'
    assert 'SECRET' not in response.get_data(as_text=True)
    monkeypatch.setattr(store, 'save_run', save)
    assert client.post('/api/ecommerce/analyze', json=body).json['state'] == 'interrupted'
    assert client.post('/api/ecommerce/analyze', json={**body, 'request_id': 'v2-disk-002'}).json['state'] == 'success'
