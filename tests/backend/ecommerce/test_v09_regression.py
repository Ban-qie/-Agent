"""Cross-layer acceptance boundaries found during V0-9 review; offline only."""
import hashlib
import json

import pytest

from data_formulator.datalake.workspace_manager import WorkspaceManager
from data_formulator.ecommerce.analysis import bind_question
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.workspace_state import AnalysisWorkspace

BODY = {"request_id": "v09-boundary-001", "user_question": "分析2018年1月销售额"}


@pytest.mark.parametrize("host", ["untrusted.example:5567", "127.0.0.1.untrusted.example", "localhost:9999"])
def test_foreign_host_rejected_without_origin(monkeypatch, host):
    from devtools.run_local import configure_offline
    configure_offline()
    from data_formulator.app import app
    from data_formulator.auth import identity
    monkeypatch.setattr(identity, "_localhost_identity", "local:v09-test")
    monkeypatch.setattr(identity, "_provider", None)
    response = app.test_client().get("/api/ecommerce/catalog", headers={"Host": host})
    assert response.status_code == 403


def test_completed_audit_survives_crash_before_workspace_commit(tmp_path):
    from data_formulator.ecommerce.analysis_service import analyze
    audit = tmp_path / "audit"
    store = AnalysisWorkspace(WorkspaceManager(tmp_path / "workspaces"), "local:v09-test", audit_directory=audit)
    class Crash(BaseException):
        pass
    def execute():
        def disabled(_):
            raise ToolError("MODEL_DISABLED", "disabled")
        result = analyze(BODY, "local:v09-test", audit, disabled)
        assert result["state"] == "failed"
        raise Crash()
    with pytest.raises(Crash):
        store.analyze(BODY, execute)
    restored = store.read()["nodes"][0]["response"]
    assert restored["state"] == "failed" and restored["error"]["code"] == "MODEL_DISABLED"
    assert store.analyze(BODY, lambda: pytest.fail("must not execute")) == restored


def test_workspace_rejects_duplicate_nodes(tmp_path):
    manager = WorkspaceManager(tmp_path)
    store = AnalysisWorkspace(manager, "local:v09-test")
    store.analyze(BODY, lambda: {"state": "success"})
    path = store.path / "session_state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["nodes"].append(dict(state["nodes"][0]))
    path.write_text(json.dumps(state), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ToolError) as exc:
        store.read()
    assert exc.value.code == "WORKSPACE_UNAVAILABLE" and path.read_bytes() == before


def test_analysis_failure_retains_bound_conditions(tmp_path):
    store = AnalysisWorkspace(WorkspaceManager(tmp_path), "local:v09-test")
    def execute():
        raise ToolError("BUSY", "Prior execution requires review")
    response = store.analyze(BODY, execute)
    assert response["state"] == "failed"
    assert json.loads(json.dumps(response["conditions"])) == json.loads(json.dumps(bind_question(**{
        "question": BODY["user_question"], "request_id": BODY["request_id"]}).payload()))


@pytest.mark.parametrize("failure", ["model_timeout", "feedback_failure", "tool_timeout", "dangerous_tool", "changed_conditions"])
def test_http_agent_failure_is_persisted_and_never_retried(tmp_path, monkeypatch, failure):
    """Real Flask -> workspace -> analysis -> AnalystAgent, fake provider/failure injection."""
    from devtools.run_local import configure_offline
    configure_offline()
    from data_formulator.app import app
    from data_formulator.auth import identity
    from data_formulator.ecommerce import analysis_service as service
    from data_formulator.ecommerce.contracts import error_result
    from data_formulator.routes import ecommerce as routes
    from tests.backend.ecommerce.test_analysis import FakeClient, FakeExecutor
    monkeypatch.setattr(identity, "_localhost_identity", "local:v09-test")
    monkeypatch.setattr(identity, "_provider", None)
    audit = tmp_path / 'audit'
    store = AnalysisWorkspace(WorkspaceManager(tmp_path / 'workspaces'), 'local:v09-test', audit_directory=audit)
    monkeypatch.setattr(routes, '_workspace', lambda _: store)
    def mutate(calls):
        if failure == 'dangerous_tool':
            calls[0].function.name = 'execute_python_script'
        if failure == 'changed_conditions':
            args = json.loads(calls[0].function.arguments)
            args['regions'] = ['SP']
            calls[0].function.arguments = json.dumps(args)
        return calls
    class Client(FakeClient):
        last_error = None
        def get_completion_with_tools(self, *args, **kwargs):
            if failure == 'model_timeout' or (failure == 'feedback_failure' and self.calls):
                self.calls += 1
                self.last_error = error_result('MODEL_FAILED', 'Accounted model request failed')
                raise ToolError('MODEL_FAILED', 'Accounted model request failed')
            return super().get_completion_with_tools(*args, **kwargs)
    class Executor(FakeExecutor):
        def execute(self, *args):
            if failure == 'tool_timeout':
                self.calls += 1
                return error_result('EXECUTION_TIMEOUT', 'Worker deadline exceeded')
            return super().execute(*args)
    provider = Client(bind_question(BODY['user_question'], BODY['request_id']), mutate)
    executor = Executor()
    original = service.analyze
    monkeypatch.setattr(service, 'MetricExecutor', lambda _: executor)
    monkeypatch.setattr(service, 'analyze', lambda body, who, directory:
                        original(body, who, audit, lambda _: provider))
    client = app.test_client()
    result = client.post('/api/ecommerce/analyze', json=BODY)
    assert result.status_code == 422 and result.json['state'] == 'failed'
    assert '999999999' not in json.dumps(result.json)
    if failure == 'feedback_failure':
        assert result.json['partial_result']['values']['sales_amount'] == '12.34'
        assert result.json['agent_completed'] is False
    if failure in ('model_timeout', 'dangerous_tool', 'changed_conditions'):
        assert executor.calls == 0
    calls = provider.calls
    assert calls <= 2
    assert client.post('/api/ecommerce/analyze', json=BODY).json == result.json
    assert client.get('/api/ecommerce/workspace').json['nodes'][0]['response'] == result.json
    assert provider.calls == calls


def test_recovery_cannot_take_another_identity_or_question_audit(tmp_path):
    from data_formulator.ecommerce.analysis_service import completed_response
    path = tmp_path / 'analysis-audit.json'
    key = hashlib.sha256(('local:other\0' + BODY['request_id']).encode()).hexdigest()
    record = {'fingerprint': hashlib.sha256(json.dumps(BODY, sort_keys=True).encode()).hexdigest(),
              'status': 'completed', 'response': {'state': 'success'}}
    path.write_text(json.dumps({key: record}), encoding='utf-8')
    assert completed_response(BODY, 'local:v09-test', tmp_path) is None
    with pytest.raises(ToolError):
        completed_response({**BODY, 'user_question': 'changed'}, 'local:other', tmp_path)
