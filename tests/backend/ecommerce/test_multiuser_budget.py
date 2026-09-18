import json
from pathlib import Path

import pytest

from data_formulator.ecommerce.atomic_budget import AtomicBudget, RESERVATION
from data_formulator.ecommerce.budget import UsageLedger
from data_formulator.ecommerce.contracts import ToolError
from tests.backend.ecommerce.test_task_store import tasks, body


def prepare(store, tmp_path, a, b, cap='.06'):
    source = tmp_path / 'usage.json'
    real = Path(__file__).resolve().parents[3] / '.local/verification/qwen-usage.json'
    if real.exists():
        source.write_bytes(real.read_bytes())
    else:
        source.write_text(json.dumps([{'attempt': i+1, 'reserved_cny': .02} for i in range(5)]))
    budget = AtomicBudget(store, source)
    budget.migrate_copy(live_cap_cny=cap)
    budget.set_user_cap(a, '.06')
    budget.set_user_cap(b, '.06')
    return budget


def test_legacy_conservation_and_disabled_old_writer(tasks, tmp_path):
    store, a, b = tasks
    budget = prepare(store, tmp_path, a, b)
    rows = UsageLedger(budget.source).read()
    from data_formulator.ecommerce.atomic_budget import money
    assert budget.totals()['total'] == sum(max(money(r['reserved_cny']), money(r.get('estimated_cny', 0))) for r in rows)
    assert budget.source.read_bytes() == budget.source.with_suffix('.migration-backup.json').read_bytes()
    for action in [lambda: UsageLedger(budget.source).reserve('old'), lambda: UsageLedger(budget.source).finish(1, {})]:
        with pytest.raises(ToolError) as error:
            action()
        assert error.value.code == 'BUDGET_UNAVAILABLE'


def test_reserve_settle_idempotence_and_unknown_retained(tasks, tmp_path):
    store, a, b = tasks
    budget = prepare(store, tmp_path, a, b)
    created, _ = store.create_or_get(a, 'ecommerce-v0', body())
    task = store.claim(created['id'])
    reservation = budget.reserve(task, 1)
    with pytest.raises(ToolError):
        budget.reserve(task, 1)
    budget.finish(reservation)
    budget.finish(reservation)
    assert budget.totals()['new'] == RESERVATION
    with pytest.raises(ToolError):
        budget.finish(reservation, {'input_tokens': 1, 'output_tokens': 1})
    restarted = AtomicBudget(store, budget.source)
    assert restarted.totals() == budget.totals()
    budget.set_user_cap(a, '.02')
    with pytest.raises(ToolError) as error:
        budget.reserve(task, 2)
    assert error.value.code == 'BUDGET_EXHAUSTED'
    other, _ = store.create_or_get(b, 'ecommerce-v0', body())
    assert budget.reserve(store.claim(other['id']), 1)


def test_zero_live_budget_and_missing_activation_fail_closed(tasks, tmp_path):
    store, a, b = tasks
    budget = prepare(store, tmp_path, a, b, cap=0)
    created, _ = store.create_or_get(a, 'ecommerce-v0', body())
    task = store.claim(created['id'])
    with pytest.raises(ToolError) as error:
        budget.reserve(task, 1)
    assert error.value.code == 'BUDGET_EXHAUSTED'
    budget.source.with_suffix('.migrated').rename(tmp_path / 'preserved-marker')
    with pytest.raises(ToolError) as error:
        budget.reserve(task, 1)
    assert error.value.code == 'BUDGET_UNAVAILABLE'


def _reserve_race(path, source, task, barrier, output):
    from data_formulator.ecommerce.task_store import TaskStore
    store = TaskStore(path)
    budget = AtomicBudget(store, source)
    barrier.wait(timeout=15)
    try:
        reservation = budget.reserve(task, 1)
        with store.transaction() as db:
            db.execute('INSERT INTO dispatch_calls VALUES(?)', (reservation,))
        output.put('dispatched')
    except ToolError as error:
        output.put(error.code)


def test_project_last_reservation_two_processes(tasks, tmp_path):
    import multiprocessing
    store, a, b = tasks
    source = tmp_path / 'critical.json'
    source.write_text(json.dumps([{'attempt': i+1, 'reserved_cny': 1.996} for i in range(5)]))
    budget = AtomicBudget(store, source)
    budget.migrate_copy(live_cap_cny='.06')
    with store.transaction() as db:
        db.execute('CREATE TABLE dispatch_calls(reservation TEXT)')
    pending = []
    for owner in (a, b):
        budget.set_user_cap(owner, '.06')
        task, _ = store.create_or_get(owner, 'ecommerce-v0', body())
        pending.append(store.claim(task['id']))
    ctx = multiprocessing.get_context('spawn')
    barrier, output = ctx.Barrier(2), ctx.Queue()
    processes = [ctx.Process(target=_reserve_race, args=(store.path, source, task, barrier, output)) for task in pending]
    try:
        for process in processes:
            process.start()
        outcomes = [output.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert sorted(outcomes) == ['BUDGET_EXHAUSTED', 'dispatched']
        with store.transaction() as db:
            assert db.execute('SELECT count(*) FROM reservations').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM dispatch_calls').fetchone()[0] == 1
        assert budget.totals()['total'] == 10000000000
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        output.close()
