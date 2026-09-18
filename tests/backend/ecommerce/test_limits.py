import pytest

from data_formulator.ecommerce.contracts import ToolError
from tests.backend.ecommerce.test_task_store import tasks, body


def test_slots_and_rate_limit_owner_fairness_restart(tasks):
    store, a, b = tasks
    first, _ = store.create_or_get(a, 'ecommerce-v0', body())
    with pytest.raises(ToolError) as error:
        store.create_or_get(a, 'ecommerce-v0', {**body(), 'request_id': 'another-001'})
    assert error.value.code == 'BUSY'
    other, _ = store.create_or_get(b, 'ecommerce-v0', body())
    assert other['id'] != first['id']
    store.finish_if_owner_version(store.claim(first['id']), {'state': 'failed'})
    store.finish_if_owner_version(store.claim(other['id']), {'state': 'failed'})
    for index in range(5):
        item, _ = store.create_or_get(a, 'ecommerce-v0', {**body(), 'request_id': f'next-id-{index}'})
        store.finish_if_owner_version(store.claim(item['id']), {'state': 'success'})
    from data_formulator.ecommerce.task_store import TaskStore
    reopened = TaskStore(store.path)
    with pytest.raises(ToolError) as error:
        reopened.create_or_get(a, 'ecommerce-v0', {**body(), 'request_id': 'rate-over-001'})
    assert error.value.code == 'RATE_LIMIT'
    assert reopened.create_or_get(b, 'ecommerce-v0', {**body(), 'request_id': 'b-normal-001'})[1]


def test_unknown_fee_prevents_repeat_dispatch(tasks, tmp_path):
    from unittest.mock import Mock
    from data_formulator.ecommerce.governed_client import GovernedClient
    from tests.backend.ecommerce.test_multiuser_budget import prepare
    store, a, b = tasks
    budget = prepare(store, tmp_path, a, b)
    item, _ = store.create_or_get(a, 'ecommerce-v0', body())
    task = store.claim(item['id'])
    wire = Mock()
    wire.get_completion.side_effect = TimeoutError('offline injected')
    client = GovernedClient(wire, budget, task, store)
    with pytest.raises(TimeoutError):
        client.get_completion([])
    restarted = GovernedClient(wire, budget, task, store)
    with pytest.raises(ToolError) as error:
        restarted.get_completion([])
    assert error.value.code == 'INTERRUPTED'
    assert wire.get_completion.call_count == 1
    assert budget.totals()['new'] == 20000000
