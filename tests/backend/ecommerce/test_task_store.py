import pytest

from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.task_store import TaskStore, input_fingerprint, terminal_status, transition, TERMINAL


def test_state_mapping_and_no_terminal_rewrite():
    for state in TERMINAL:
        assert terminal_status({'state': state}) == state
        with pytest.raises(ToolError):
            transition(state, 'running')
    assert terminal_status({'state': 'partial'}) == 'failed'
    assert terminal_status({'state': 'clarification_required'}) == 'waiting_clarification'
    assert transition('accepted', 'running') == 'running'
    with pytest.raises(ToolError):
        transition('accepted', 'success')
    with pytest.raises(ToolError):
        terminal_status({'state': 'made-up'})
    assert input_fingerprint({'request_id': 'x'}) == input_fingerprint({'request_id': 'x', 'parent_node_id': None})


@pytest.fixture
def tasks(tmp_path):
    store = TaskStore(tmp_path / 'db.sqlite')
    store.initialize()
    a, b = [store.create(n, 'test-password') for n in ('alice', 'bobby')]
    for owner in (a, b):
        store.create_workspace(owner)
    return store, a, b


def body(question='分析2018年1月销售额'):
    return {'request_id': 'request-001', 'user_question': question}


def test_idempotency_and_claim_fence(tasks):
    store, a, b = tasks
    first, created = store.create_or_get(a, 'ecommerce-v0', body())
    assert created
    again, created = store.create_or_get(a, 'ecommerce-v0', body())
    assert not created and first['id'] == again['id']
    with pytest.raises(ToolError) as error:
        store.create_or_get(a, 'ecommerce-v0', body('different'))
    assert error.value.code == 'REQUEST_CONFLICT'
    other, _ = store.create_or_get(b, 'ecommerce-v0', body())
    assert other['id'] != first['id']
    with pytest.raises(ToolError):
        store.get_authorized(b, 'ecommerce-v0', first['id'])
    claimed = store.claim(first['id'])
    with pytest.raises(ToolError):
        store.claim(first['id'])
    response = {'state': 'partial', 'result': {'kept': 12}}
    final = store.finish_if_owner_version(claimed, response)
    assert final['status'] == 'failed' and 'kept' in final['response']
    with pytest.raises(ToolError):
        store.finish_if_owner_version(claimed, {'state': 'success'})
    assert store.request_cancel(a, 'ecommerce-v0', first['id']) == final


def test_active_task_limit_is_store_specific(tasks):
    store, a, b = tasks
    store.max_active_tasks = 1
    store.create_or_get(a, 'ecommerce-v0', body())
    other = body()
    other['request_id'] = 'request-002'
    with pytest.raises(ToolError) as error:
        store.create_or_get(b, 'ecommerce-v0', other)
    assert error.value.code == 'BUSY'


def test_expired_lease_never_replays(tasks):
    store, a, _ = tasks
    now = [100.0]
    store.clock = lambda: now[0]
    task, _ = store.create_or_get(a, 'ecommerce-v0', body())
    claimed = store.claim(task['id'])
    now[0] += 76
    with pytest.raises(ToolError):
        store.finish_if_owner_version(claimed, {'state': 'success'})
    assert store.recover_expired() == 1
    restored, created = store.create_or_get(a, 'ecommerce-v0', body())
    assert restored['status'] == 'interrupted' and not created


def _race_create_claim(path, owner, barrier, results):
    store = TaskStore(path)
    barrier.wait(timeout=15)
    task, _ = store.create_or_get(owner, 'ecommerce-v0', body())
    try:
        claimed = store.claim(task['id'])
        # Durable, externally observable stand-in for the graph invocation.
        with store.transaction() as db:
            db.execute('INSERT INTO graph_calls VALUES(?)', (task['id'],))
        store.finish_if_owner_version(claimed, {'state': 'success'})
        results.put('executed')
    except ToolError as error:
        results.put(error.code)


def test_two_independent_processes_one_task_one_graph(tasks):
    import multiprocessing
    store, a, _ = tasks
    with store.transaction() as db:
        db.execute('CREATE TABLE graph_calls(task TEXT)')
    ctx = multiprocessing.get_context('spawn')
    barrier, results = ctx.Barrier(2), ctx.Queue()
    children = [ctx.Process(target=_race_create_claim, args=(store.path, a, barrier, results)) for _ in range(2)]
    try:
        for process in children:
            process.start()
        outcomes = [results.get(timeout=30) for _ in children]
        for process in children:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert sorted(outcomes) == ['VERSION_CONFLICT', 'executed']
        with store.transaction() as db:
            assert db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM graph_calls').fetchone()[0] == 1
    finally:
        for process in children:
            if process.is_alive():
                process.terminate()
                process.join()
        results.close()
