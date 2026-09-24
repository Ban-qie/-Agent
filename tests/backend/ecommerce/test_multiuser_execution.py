from unittest.mock import Mock

import pytest

from data_formulator.ecommerce.authorization import Principal
from data_formulator.ecommerce.contracts import ToolError, parse_request
from data_formulator.ecommerce.execution_context import ExecutionContext
from data_formulator.ecommerce.executor import MetricExecutor, SNAPSHOT_ID
from data_formulator.ecommerce.metrics import METRIC_VERSION
from data_formulator.ecommerce.workspace_repository import WorkspaceRepository


def request_payload():
    return dict(request_id='shared-request', snapshot_id=SNAPSHOT_ID, metric_version=METRIC_VERSION,
                operation='summarize', current={'start': '2018-01-01', 'end': '2018-02-01'})


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    repo = WorkspaceRepository(tmp_path / 'multiuser.sqlite')
    repo.initialize()
    a, b = (repo.create(name, 'offline-password') for name in ('alice', 'bobby'))
    for owner in (a, b):
        repo.create_workspace(owner)
    worker = Mock(return_value={'state': 'success', 'values': {'sales_amount': '12.00'}})
    monkeypatch.setattr('data_formulator.ecommerce.executor.run_worker', worker)
    executors = [MetricExecutor(tmp_path / 'audit.json', context=ExecutionContext(Principal(o), 'ecommerce-v0', repo)) for o in (a, b)]
    return repo, a, b, executors, worker


def test_authorized_context_and_forged_snapshot_workspace(fixture, tmp_path):
    repo, a, b, executors, worker = fixture
    req = parse_request(request_payload())
    assert executors[0].execute(a, req)['state'] == 'success'
    assert worker.call_count == 1
    for owner, payload, executor in [
        (b, request_payload(), executors[0]),
        (a, {**request_payload(), 'snapshot_id': '0' * 64}, executors[0]),
        (a, request_payload(), MetricExecutor(tmp_path / 'bad.json', context=ExecutionContext(Principal(a), '../other', repo))),
    ]:
        with pytest.raises(ToolError):
            executor.execute(owner, parse_request(payload))
    assert worker.call_count == 1


def test_same_request_cache_and_audit_isolated(fixture):
    repo, a, b, executors, worker = fixture
    req = parse_request(request_payload())
    for owner, executor in zip((a, b), executors):
        assert executor.execute(owner, req)['state'] == 'success'
        assert executor.execute(owner, req)['state'] == 'success'
    assert worker.call_count == 2
    assert executors[0].audit_path != executors[1].audit_path
    payload = request_payload()
    payload['current'] = {'start': '2018-02-01', 'end': '2018-03-01'}
    with pytest.raises(ToolError) as error:
        executors[0].execute(a, parse_request(payload))
    assert error.value.code == 'REQUEST_CONFLICT'
    assert worker.call_count == 2


def test_service_passes_server_catalog_to_executor(tmp_path, monkeypatch):
    from data_formulator.ecommerce.multiuser_service import MultiuserService

    catalog = {SNAPSHOT_ID: {'path': '/opt/ecommerce/frozen/orders.parquet',
                             'sha256': '0' * 64, 'quality': {}}}
    captured = {}

    class Executor:
        def __init__(self, audit_path, catalog=None, *, context=None):
            captured.update(audit_path=audit_path, catalog=catalog, context=context)

    monkeypatch.setattr('data_formulator.ecommerce.multiuser_service.MetricExecutor', Executor)
    service = MultiuserService(Mock(), tmp_path / 'audit', Mock(), catalog=catalog)
    principal = Principal('owner-id')
    workspace = Mock()
    client = Mock()

    monkeypatch.setattr('data_formulator.ecommerce.multiuser_service.validate_analyze_request', lambda body: None)
    monkeypatch.setattr('data_formulator.ecommerce.multiuser_service.analyze_v1', lambda *args, **kwargs: {'state': 'success'})
    assert service.analyze(principal, {'request_id': 'catalog-pass-001'},
                           workspace_override=workspace, client_override=client)['state'] == 'success'
    assert captured['catalog'] is catalog
    assert captured['context'].principal == principal


def test_two_password_users_route_and_parent_boundary(tmp_path):
    import secrets
    from data_formulator.ecommerce.multiuser_app import create_app
    from data_formulator.ecommerce.multiuser_service import MultiuserService
    from data_formulator.ecommerce.multiuser_routes import install_routes
    from tests.backend.ecommerce.test_multiuser_auth import login, get, ORIGIN
    from tests.backend.ecommerce.test_v1_agents import Client, answers
    repo = WorkspaceRepository(tmp_path / 'multiuser.sqlite')
    repo.initialize()
    for name in ('alice', 'bobby'):
        repo.create(name, 'test-password-' + ('A' if name == 'alice' else 'B'))
    clients = []
    def factory(principal, request_id):
        client = Client(answers())
        clients.append(client)
        return client
    app = create_app(tmp_path, secret_key=secrets.token_hex(32))
    service = MultiuserService(repo, tmp_path / 'audit', factory)
    install_routes(app, service)
    a, b = app.test_client(), app.test_client()
    ca = login(a).json['csrf_token']
    cb = login(b, 'bobby', 'test-password-B').json['csrf_token']
    def post(client, csrf, body):
        return client.post('/api/ecommerce/analyze', base_url=ORIGIN, json=body,
                           headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf})
    body = {'request_id': 'user-root-001', 'user_question': '分析2018年1月销售额'}
    assert post(a, ca, body).json['state'] == 'success'
    assert get(b, '/api/ecommerce/workspace').json['nodes'] == []
    before = len(clients)
    assert post(b, cb, {**body, 'request_id': 'child-0001', 'parent_node_id': body['request_id']}).status_code == 404
    assert post(b, cb, {**body, 'owner': 'alice'}).status_code == 400
    assert len(clients) == before
    assert post(b, cb, body).json['state'] == 'success'
    assert len(get(a, '/api/ecommerce/workspace').json['nodes']) == 1
    assert len(get(b, '/api/ecommerce/workspace').json['nodes']) == 1
