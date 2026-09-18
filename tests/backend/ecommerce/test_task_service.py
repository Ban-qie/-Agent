import threading

import pytest

from data_formulator.ecommerce.authorization import Principal
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.multiuser_service import MultiuserService
from data_formulator.ecommerce.task_service import TaskService
from tests.backend.ecommerce.test_task_store import tasks, body


def test_running_replay_conflict_and_atomic_final_node(tasks, tmp_path):
    store, a, b = tasks
    entered, release = threading.Event(), threading.Event()
    calls = []
    business = MultiuserService(store, tmp_path, lambda *args: None)
    def execute(principal, payload, *, workspace_override, client_override, checkpoint):
        calls.append(principal.owner)
        entered.set()
        assert release.wait(10)
        response = {'state': 'success', 'result': {'answer': 'saved'}}
        workspace_override.save_run(node_id=payload['request_id'], question=payload['user_question'],
                                    conditions={}, status='success', result=response, parent_node_id=None)
        return response
    business.analyze = execute
    service = TaskService(store, business)
    try:
        first = service.submit(Principal(a), body())
        assert entered.wait(10)
        again = service.submit(Principal(a), body())
        assert again['id'] == first['id'] and again['status'] == 'running'
        assert store.read(a, 'ecommerce-v0')['nodes'] == []
        with pytest.raises(ToolError) as error:
            service.submit(Principal(a), body('different'))
        assert error.value.code == 'REQUEST_CONFLICT'
        with pytest.raises(ToolError):
            service.get(Principal(b), first['id'])
    finally:
        release.set()
        service.close()
    final = service.get(Principal(a), first['id'])
    assert final['status'] == 'success'
    assert store.read(a, 'ecommerce-v0')['nodes'][0]['result']['result'] == {'answer': 'saved'}
    assert service.submit(Principal(a), body())['status'] == 'success'
    assert calls == [a]


def test_task_http_202_get_cancel_scope_and_read_only(tasks, tmp_path):
    import secrets
    from data_formulator.ecommerce.multiuser_app import create_app
    from data_formulator.ecommerce.multiuser_routes import install_routes
    from tests.backend.ecommerce.test_multiuser_auth import login, get, ORIGIN
    store, a, b = tasks
    # Factory requires the agreed filename, use SQLite backup then reopen.
    target = tmp_path / 'multiuser.sqlite'
    store.backup(target)
    from data_formulator.ecommerce.task_store import TaskStore
    store = TaskStore(target)
    entered, release = threading.Event(), threading.Event()
    business = MultiuserService(store, tmp_path, lambda *args: None)
    calls = []
    def execute(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(10)
        return {'state': 'success'}
    business.analyze = execute
    service = TaskService(store, business)
    app = create_app(tmp_path, secret_key=secrets.token_hex(32))
    install_routes(app, service)
    ca, cb = app.test_client(), app.test_client()
    csrf = login(ca, password='test-password').json['csrf_token']
    csrf_b = login(cb, 'bobby', 'test-password').json['csrf_token']
    headers = {'Origin': ORIGIN, 'X-CSRF-Token': csrf}
    try:
        response = ca.post('/api/ecommerce/analyze', base_url=ORIGIN, headers=headers, json=body())
        assert response.status_code == 202
        task_id = response.json['task_id']
        assert entered.wait(10)
        path = '/api/ecommerce/tasks/' + task_id
        before = service.get(Principal(a), task_id)
        assert get(ca, path).json['status'] == 'running'
        assert service.get(Principal(a), task_id) == before
        assert get(cb, path).status_code == 404
        assert cb.post(path + '/cancel', base_url=ORIGIN,
                       headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf_b}).status_code == 404
        assert get(cb, '/api/ecommerce/workspace').json['tasks'] == []
        assert ca.post(path + '/cancel', base_url=ORIGIN, headers=headers).json['cancel_requested']
    finally:
        release.set()
        service.close()
    assert get(ca, path).json['status'] == 'cancelled'
    assert calls == [1]
