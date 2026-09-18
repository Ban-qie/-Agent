from copy import deepcopy
import json

import pytest

from devtools.v1_campaign import digest, verify_ledger, confined, resume_action, create_once


def test_ledger_prefix_and_absolute_cap_survive_restarts():
    rows = [{'attempt': n, 'reserved_cny': .02} for n in range(1, 64)]
    before = deepcopy(rows)
    fingerprint = digest(before)
    rows.extend({'attempt': n, 'reserved_cny': .02} for n in range(64, 194))
    verify_ledger(rows, 63, fingerprint)
    with pytest.raises(ValueError): verify_ledger(rows, 63, fingerprint, needed=1)
    with pytest.raises(ValueError): verify_ledger(before[:-1], 63, fingerprint)
    rows[0]['reserved_cny'] = 0
    with pytest.raises(ValueError): verify_ledger(rows, 63, fingerprint)


def test_resume_before_submission_and_after_durable_response(tmp_path):
    body = {'request_id': 'r04-test-001', 'user_question': 'question'}
    path = tmp_path / 'intent.json'
    create_once(path, body)
    assert resume_action(body, None, []) == 'submit_same_id'
    node = {'node_id': body['request_id'], 'question': 'question', 'status': 'success', 'result': {'state': 'success'}}
    rows = [{'task_id': 'v1-team:r04-test-001'}]
    # Response/evidence lost: recover persisted result, no new submit.
    assert resume_action(body, node, rows) == 'recover'
    with pytest.raises(FileExistsError): create_once(path, {})
    assert json.loads(path.read_text()) == body
    with pytest.raises(ValueError): resume_action({**body, 'user_question': 'other'}, node, rows)
    with pytest.raises(ValueError): resume_action(body, None, rows)
    for status in ['running', 'failed', 'interrupted']:
        with pytest.raises(ValueError): resume_action(body, {**node, 'status': status}, rows)


def test_bad_parent_and_path_are_rejected(tmp_path):
    body = {'request_id': 'r04-test-002', 'user_question': 'q', 'parent_node_id': 'r04-test-001'}
    with pytest.raises(ValueError): resume_action(body, None, [], None)
    with pytest.raises(ValueError): resume_action(body, None, [], {'node_id': 'r04-test-001', 'status': 'failed'})
    with pytest.raises(ValueError): confined(tmp_path / '../outside', tmp_path)
    assert confined(tmp_path / 'inside', tmp_path).parent == tmp_path


@pytest.mark.parametrize('cap', [193, 247])
def test_atomic_reserve_checks_persistent_task_cap_and_total_cap(tmp_path, monkeypatch, cap):
    from data_formulator.ecommerce.budget import UsageLedger, BudgetClient
    from data_formulator.ecommerce.contracts import ToolError
    from devtools.v1_campaign_run import install_capture
    rows = [{'attempt': n, 'reserved_cny': .02, 'task_id': 'old'} for n in range(1, 6)]
    path = tmp_path / 'ledger.json'
    path.write_text(json.dumps(rows))
    # Register originals for fixture teardown before the harness wraps them.
    for cls, attr in [(UsageLedger, 'read'), (UsageLedger, 'reserve'), (BudgetClient, '_dispatch')]:
        monkeypatch.setattr(cls, attr, getattr(cls, attr))
    install_capture({'prefix_count': 5, 'prefix_hash': digest(rows), 'absolute_call_cap': cap}, tmp_path, 'fake-key')
    ledger = UsageLedger(path)
    for _ in range(3): ledger.reserve('v1-team:new-task')
    with pytest.raises(ToolError): UsageLedger(path).reserve('v1-team:new-task')
    assert len(ledger.read()) == 8 and not path.with_suffix('.lock').exists()
    for i in range(9, cap + 1):
        data = json.loads(path.read_text())
        data.append({'attempt': i, 'reserved_cny': .02, 'task_id': 'external'})
        path.write_text(json.dumps(data))
    with pytest.raises(ToolError): ledger.reserve('v1-team:another-task')
    assert len(ledger.read()) == cap and not path.with_suffix('.lock').exists()


def test_capture_redacts_and_never_retries(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from data_formulator.ecommerce.budget import UsageLedger, BudgetClient
    from devtools.v1_campaign_run import install_capture, safe_capture
    calls = []
    def fake(self, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"fact_ids":["secret-test-key"]}'), finish_reason='stop')])
    monkeypatch.setattr(BudgetClient, '_dispatch', fake)
    monkeypatch.setattr(UsageLedger, 'read', UsageLedger.read)
    monkeypatch.setattr(UsageLedger, 'reserve', UsageLedger.reserve)
    install_capture({'prefix_count': 0, 'prefix_hash': digest([]), 'absolute_call_cap': 193}, tmp_path, 'secret-test-key')
    client = SimpleNamespace(task_id='v1-team:test-capture')
    BudgetClient._dispatch(client, messages=[{'content': 'Your role is evidence Interpreter'},
        {'content': '{"facts":{"scope":"verified"}}'}], stream=False, params={})
    capture = json.loads(next(tmp_path.glob('model-*.json')).read_text())
    assert capture['classification'] == 'unknown_id'
    assert 'secret-test-key' not in str(capture) and len(calls) == 1
    assert len(safe_capture('x' * 10000, 'key')) == 8192
    assert 'sk-fake' not in safe_capture('Bearer token sk-fake', '')
