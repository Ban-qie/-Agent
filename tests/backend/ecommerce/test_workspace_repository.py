import pytest

from data_formulator.ecommerce.authorization import Principal, ResourceRef, authorize
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.workspace_repository import WorkspaceRepository


def node(identity='node-0001', parent=None, status='success', answer='A-result'):
    return {'node_id': identity, 'parent_node_id': parent, 'question': '问题', 'status': status,
            'conditions': {'metric_version': 'kept'}, 'result': {'answer': answer}, 'error': {}, 'chart_spec': {}}


@pytest.fixture
def repo(tmp_path):
    value = WorkspaceRepository(tmp_path / 'multiuser.sqlite')
    value.initialize()
    a = value.create('alice', 'test-password-A')
    b = value.create('bobby', 'test-password-B')
    for owner in (a, b):
        value.create_workspace(owner)
    return value, a, b


def test_roundtrip_and_unknown_scope(repo):
    store, a, b = repo
    store.save_node(a, 'ecommerce-v0', node(), expected_version=0)
    assert WorkspaceRepository(store.path).get_node(a, 'ecommerce-v0', 'node-0001') == node()
    assert store.read(b, 'ecommerce-v0')['nodes'] == []
    with pytest.raises(ToolError, match='Resource not found'):
        store.get_node(b, 'ecommerce-v0', 'node-0001')


def test_real_scoped_authorization_and_same_ids(repo):
    store, a, b = repo
    store.save_node(a, 'ecommerce-v0', node(), expected_version=0)
    store.save_node(b, 'ecommerce-v0', node(answer='B-private'), expected_version=0)
    for owner, answer in [(a, 'A-result'), (b, 'B-private')]:
        assert authorize(Principal(owner), 'read', ResourceRef('node', 'ecommerce-v0', 'node-0001'), store)['owner'] == owner
        assert store.get_node(owner, 'ecommerce-v0', 'node-0001')['result']['answer'] == answer
    store.save_node(b, 'ecommerce-v0', node('B-parent'), expected_version=1)
    with pytest.raises(ToolError):
        store.save_node(a, 'ecommerce-v0', node('child-001', 'B-parent'), expected_version=1)
    assert len(store.read(a, 'ecommerce-v0')['nodes']) == 1


def _competing_save(path, owner, identity, barrier, results):
    store = WorkspaceRepository(path)
    barrier.wait(timeout=15)
    try:
        store.save_node(owner, 'ecommerce-v0', node(identity), expected_version=0)
        results.put('saved')
    except ToolError as error:
        results.put(error.code)


def test_two_process_version_conflict_no_lost_update(repo):
    import multiprocessing
    store, a, _ = repo
    ctx = multiprocessing.get_context('spawn')
    barrier, results = ctx.Barrier(2), ctx.Queue()
    processes = [ctx.Process(target=_competing_save, args=(store.path, a, f'node-000{i}', barrier, results)) for i in range(2)]
    try:
        for p in processes:
            p.start()
        outputs = [results.get(timeout=30) for _ in processes]
        for p in processes:
            p.join(timeout=10)
            assert p.exitcode == 0
        assert sorted(outputs) == ['VERSION_CONFLICT', 'saved']
        state = store.read(a, 'ecommerce-v0')
        assert state['version'] == 1 and len(state['nodes']) == 1
    finally:
        for p in processes:
            if p.is_alive():
                p.terminate()
                p.join()
        results.close()


def test_rollback_capacity_and_consistent_backup(repo, tmp_path, monkeypatch):
    store, a, _ = repo
    def fail():
        raise OSError('simulated commit failure')
    with pytest.raises(OSError):
        store.save_node(a, 'ecommerce-v0', node(), expected_version=0, before_commit=fail)
    assert store.read(a, 'ecommerce-v0')['version'] == 0
    assert store.read(a, 'ecommerce-v0')['nodes'] == []
    store.save_node(a, 'ecommerce-v0', node(), expected_version=0)
    import data_formulator.ecommerce.workspace_repository as module
    monkeypatch.setattr(module, 'MAX_NODES', 1)
    with pytest.raises(ToolError) as error:
        store.save_node(a, 'ecommerce-v0', node('node-0002'), expected_version=1)
    assert error.value.code == 'RESOURCE_LIMIT'
    monkeypatch.setattr(module, 'MAX_NODES', 128)
    monkeypatch.setattr(module, 'MAX_BYTES', 1)
    with pytest.raises(ToolError) as error:
        store.save_node(a, 'ecommerce-v0', node('node-0002'), expected_version=1)
    assert error.value.code == 'RESOURCE_LIMIT'
    target = tmp_path / 'backup.sqlite'
    store.backup(target)
    assert WorkspaceRepository(target).read(a, 'ecommerce-v0') == store.read(a, 'ecommerce-v0')
