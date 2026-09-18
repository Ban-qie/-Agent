import json
import multiprocessing
import os

import pytest

from data_formulator.ecommerce.atomic_budget import AtomicBudget
from data_formulator.ecommerce.task_store import TaskStore
from tests.backend.ecommerce.test_task_store import tasks, body
from tests.backend.ecommerce.test_multiuser_budget import prepare


def _crash(path, source, task_id, stage, ready):
    store = TaskStore(path)
    budget = AtomicBudget(store, source)
    task = store.claim(task_id)
    if stage != 'before-dispatch':
        reservation = budget.reserve(task, 1)
        if stage != 'during-model':
            budget.finish(reservation, {'input_tokens': 10, 'output_tokens': 2})
    if stage in ('after-tool', 'before-save', 'after-save'):
        from data_formulator.ecommerce.executor import run_worker, accepted_catalog, SNAPSHOT_ID
        from data_formulator.ecommerce.contracts import parse_request
        from tests.backend.ecommerce.test_multiuser_execution import request_payload
        result = run_worker(parse_request(request_payload()), accepted_catalog()[SNAPSHOT_ID])
        assert result['state'] == 'success'
    if stage == 'after-save':
        store.finish_if_owner_version(task, {'state': 'success', 'result': result})
    ready.set()
    os._exit(23)


@pytest.mark.parametrize('stage', ['before-dispatch', 'during-model', 'after-model', 'after-tool', 'before-save', 'after-save'])
def test_real_process_exit_preserves_fee_and_no_replay(tasks, tmp_path, stage):
    store, a, b = tasks
    budget = prepare(store, tmp_path, a, b)
    item, _ = store.create_or_get(a, 'ecommerce-v0', body())
    ctx = multiprocessing.get_context('spawn')
    ready = ctx.Event()
    process = ctx.Process(target=_crash, args=(store.path, budget.source, item['id'], stage, ready))
    try:
        process.start()
        assert ready.wait(30)
        process.join(timeout=5)
        assert process.exitcode == 23
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
    restored = TaskStore(store.path, clock=lambda: item['lease_expiry'] + 1)
    assert restored.recover_expired() == (0 if stage == 'after-save' else 1)
    recovered, created = restored.create_or_get(a, 'ecommerce-v0', body())
    assert not created and recovered['status'] == ('success' if stage == 'after-save' else 'interrupted')
    totals = AtomicBudget(restored, budget.source).totals()
    assert totals['new'] == (0 if stage == 'before-dispatch' else 20000000)
    with restored.transaction() as db:
        rows = db.execute('SELECT settlement FROM reservations').fetchall()
    if stage == 'during-model':
        assert rows[0]['settlement'] is None
    if stage == 'after-save':
        assert json.loads(recovered['response'])['result']['state'] == 'success'
    # Slot released; another normal task is accepted after restart without replay.
    following, created = restored.create_or_get(a, 'ecommerce-v0', {**body(), 'request_id': 'next-task-001'})
    assert created
    restored.finish_if_owner_version(restored.claim(following['id']), {'state': 'success'})
