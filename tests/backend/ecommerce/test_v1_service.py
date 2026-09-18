from pathlib import Path
import pytest

from data_formulator.ecommerce import v1_service
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.datalake.workspace_manager import WorkspaceManager
from data_formulator.ecommerce.v1_workspace import V1WorkspaceStore


def test_v1_service_maps_graph_success_to_api_response(monkeypatch, tmp_path):
    captured = {}

    def fake_graph(state, executor, identity):
        captured.update(state=state, identity=identity)
        return {
            "status": "success", "normalized_conditions": {"operation": "summarize"},
            "verified_result": {"state": "success", "values": {"order_count": 2}},
            "explanation": {"summary": "ok"}, "chart_spec": {"type": "table"},
            "trace": [{"stage": "chart_planner", "status": "success"}],
        }

    monkeypatch.setattr(v1_service, "invoke_v1_business_graph", fake_graph)
    response = v1_service.analyze_v1(
        {"request_id": "v1-api-001", "user_question": "分析2018年1月销售额"},
        "local:test", Path(tmp_path),
    )
    assert response["state"] == "success"
    assert response["result"]["values"]["order_count"] == 2
    assert captured["state"]["run_id"] == "v1-api-001"
    assert captured["identity"] == "local:test"


def test_v1_service_maps_clarification_without_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(v1_service, "invoke_v1_business_graph", lambda *args: {
        "status": "waiting_clarification", "error": {"message": "请明确期间"}, "trace": []
    })
    response = v1_service.analyze_v1(
        {"request_id": "v1-api-002", "user_question": "最近两个完整月销售额"},
        "local:test", Path(tmp_path),
    )
    assert response == {"state": "clarification_required", "question": "请明确期间",
                        "executed": False, "trace": []}


def test_v1_service_rejects_extra_request_fields(tmp_path):
    try:
        v1_service.analyze_v1({"request_id": "v1-api-003", "user_question": "x", "sql": "select 1"},
                              "local:test", Path(tmp_path))
    except ToolError as exc:
        assert exc.code == "INVALID_REQUEST"
    else:
        raise AssertionError("extra fields were accepted")


def test_v1_service_persists_result_and_parent_branch(tmp_path, monkeypatch):
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / "workspaces"), "local:test")
    conditions = {"operation": "summarize", "metrics": ["sales_amount"],
                  "current": {"start": "2018-01-01", "end": "2018-02-01"},
                  "baseline": None, "regions": [], "group_by": "region"}
    store.save_run(node_id="v1-parent-001", question="parent", conditions=conditions, status="success")
    monkeypatch.setattr(v1_service, "invoke_v1_business_graph", lambda state, executor, identity: {
        "status": "success", "normalized_conditions": {**conditions, "group_by": "day"},
        "verified_result": {"state": "success"}, "explanation": {"summary": "ok"},
        "chart_spec": {"type": "line"}, "trace": [],
    })
    response = v1_service.analyze_v1(
        {"request_id": "v1-child-001", "user_question": "按日显示销售趋势", "parent_node_id": "v1-parent-001"},
        "local:test", tmp_path / "audit", store,
    )
    assert response["state"] == "success"
    node = store.read()["nodes"][-1]
    assert node["parent_node_id"] == "v1-parent-001"
    assert node["conditions"]["group_by"] == "day"


def test_live_read_and_duplicate_do_not_interrupt_or_repeat_graph(tmp_path, monkeypatch):
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / 'workspaces'), 'local:test')
    body = {'request_id': 'v1-concurrent-001', 'user_question': '分析2018年1月销售额'}
    calls = []
    def graph(*args):
        calls.append(True)
        assert store.read()['nodes'][0]['status'] == 'running'
        with pytest.raises(ToolError) as exc:
            v1_service.analyze_v1(body, 'local:test', tmp_path, store)
        assert exc.value.code == 'BUSY'
        return {'status': 'success', 'verified_result': {'state': 'success'}, 'normalized_conditions': {}}
    monkeypatch.setattr(v1_service, 'invoke_v1_business_graph', graph)
    response = v1_service.analyze_v1(body, 'local:test', tmp_path, store)
    assert v1_service.analyze_v1(body, 'local:test', tmp_path, store) == response
    assert len(calls) == 1


def test_interrupted_request_never_reopens(tmp_path, monkeypatch):
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / 'workspaces'), 'local:test')
    store.save_run(node_id='v1-abandoned-001', question='x', conditions={}, status='running')
    monkeypatch.setattr(v1_service, 'invoke_v1_business_graph', lambda *args: pytest.fail('rerun'))
    response = v1_service.analyze_v1({'request_id': 'v1-abandoned-001', 'user_question': 'x'},
                                   'local:test', tmp_path, store)
    assert response['state'] == 'interrupted'


def test_graph_exception_is_saved_as_sanitized_failure(tmp_path, monkeypatch):
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / 'workspaces'), 'local:test')
    def fail(*args):
        raise RuntimeError('private provider detail')
    monkeypatch.setattr(v1_service, 'invoke_v1_business_graph', fail)
    response = v1_service.analyze_v1({'request_id': 'v1-failure-001', 'user_question': 'x'},
                                   'local:test', tmp_path, store)
    assert response['state'] == 'failed'
    assert 'private' not in str(response)
    assert store.read()['nodes'][0]['result'] == response


def test_v2_final_save_failure_keeps_result_and_recovery_never_reexecutes(tmp_path, monkeypatch):
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / 'workspaces'), 'local:test')
    body = {'request_id': 'v2-save-fault-001', 'user_question': '分析2018年1月销售额'}
    calls = []
    def graph(*args):
        calls.append(1)
        return {'status': 'success', 'normalized_conditions': {},
                'verified_result': {'state': 'success', 'values': {'sales_amount': '30.00'}}}
    monkeypatch.setattr(v1_service, 'invoke_v1_business_graph', graph)
    save = store.save_run
    def broken_save(**kwargs):
        if kwargs['status'] != 'running':
            raise OSError('SECRET disk path')
        return save(**kwargs)
    monkeypatch.setattr(store, 'save_run', broken_save)
    response = v1_service.analyze_v1(body, 'local:test', tmp_path, store)
    assert response['state'] == 'failed'
    assert response['error']['code'] == 'WORKSPACE_UNAVAILABLE'
    assert response['result']['values']['sales_amount'] == '30.00'
    assert 'SECRET' not in str(response)
    monkeypatch.setattr(store, 'save_run', save)
    assert v1_service.analyze_v1(body, 'local:test', tmp_path, store)['state'] == 'interrupted'
    assert calls == [1]
    next_body = {**body, 'request_id': 'v2-save-next-001'}
    assert v1_service.analyze_v1(next_body, 'local:test', tmp_path, store)['state'] == 'success'
    assert calls == [1, 1]
