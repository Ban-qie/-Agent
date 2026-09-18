import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.website_usage import WebsiteUsage
from tests.backend.ecommerce.test_task_store import tasks, body


def test_website_no_debug_cap_duplicate_and_unknown_survive_restart(tasks):
    store, owner, _ = tasks
    usage = WebsiteUsage(store)
    created, _ = store.create_or_get(owner, 'ecommerce-v0', body())
    task = store.claim(created['id'])
    def reserve():
        try:
            return usage.reserve(task, 1)
        except ToolError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert results.count('INTERRUPTED') == 1
    reservation = next(r for r in results if r != 'INTERRUPTED')
    usage.finish(reservation)  # Unknown is not discarded or replayed.
    with store.transaction() as db:
        db.execute('UPDATE website_usage SET estimated=20000000000')
    restarted = WebsiteUsage(store)
    second = restarted.reserve(task, 2)  # Above old 10 CNY cap remains usable.
    restarted.finish(second, {'input_tokens': 10, 'output_tokens': 2})
    with store.transaction() as db:
        assert db.execute('SELECT count(*) FROM website_usage').fetchone()[0] == 2
        assert json.loads(db.execute('SELECT settlement FROM website_usage WHERE id=?', (reservation,)).fetchone()[0]) == {'usage': 'unknown'}
    store.request_cancel(owner, 'ecommerce-v0', task['id'])
    with pytest.raises(ToolError):
        restarted.reserve(task, 3)


def test_real_entry_login_graph_transport_and_replay(tasks, tmp_path, monkeypatch):
    from devtools.run_v3 import build_app
    from devtools.v3_server import OfflineModel
    from data_formulator.ecommerce.qwen_client import QwenClient
    from tests.backend.ecommerce.test_multiuser_auth import login, get, ORIGIN
    store, owner, _ = tasks
    store.backup(tmp_path / 'multiuser.sqlite')
    calls = []
    def transport(client, *, messages, stream, params):
        assert client.endpoint == 'openai'
        assert params['num_retries'] == params['max_retries'] == 0
        assert params['api_base'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        client.checkpoint()
        calls.append(1)
        return OfflineModel().get_completion(messages)
    monkeypatch.setattr('data_formulator.ecommerce.model_transport.dispatch', transport)
    monkeypatch.setattr('data_formulator.ecommerce.qwen_client.configured_qwen',
        lambda *args: QwenClient.from_config(dict(endpoint='openai', model='qwen-flash',
            api_key='test-only', api_base='https://dashscope.aliyuncs.com/compatible-mode/v1')))
    app, service = build_app(tmp_path, secrets.token_hex(32))
    client = app.test_client()
    try:
        assert calls == []
        assert client.get('/', base_url=ORIGIN).status_code == 302
        anonymous_csrf = get(client, '/api/ecommerce/auth/status').json['csrf_token']
        assert client.post('/api/ecommerce/analyze', base_url=ORIGIN, json=body(),
            headers={'Origin': ORIGIN, 'X-CSRF-Token': anonymous_csrf}).status_code == 401
        csrf = login(client, password='test-password').json['csrf_token']
        headers = {'Origin': ORIGIN, 'X-CSRF-Token': csrf}
        response = client.post('/api/ecommerce/analyze', base_url=ORIGIN, json=body(), headers=headers)
        assert response.status_code == 202
        path = '/api/ecommerce/tasks/' + response.json['task_id']
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            result = get(client, path).json
            if result['status'] not in ('accepted', 'running'):
                break
            time.sleep(.05)
        assert result['status'] == 'success', result
        assert len(calls) == 3
        client.post('/api/ecommerce/analyze', base_url=ORIGIN, json=body(), headers=headers)
        assert len(calls) == 3
        with service.store.transaction() as db:
            rows = db.execute('SELECT * FROM website_usage').fetchall()
        assert len(rows) == 3 and all(row['owner'] == owner and row['settlement'] for row in rows)
    finally:
        service.close()
