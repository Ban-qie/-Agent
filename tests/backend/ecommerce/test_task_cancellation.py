import pytest

from data_formulator.ecommerce.contracts import ToolError
from tests.backend.ecommerce.test_task_store import tasks, body


def test_cancel_before_claim_and_foreign_cancel(tasks):
    store, a, b = tasks
    item, _ = store.create_or_get(a, 'ecommerce-v0', body())
    with pytest.raises(ToolError):
        store.request_cancel(b, 'ecommerce-v0', item['id'])
    assert store.request_cancel(a, 'ecommerce-v0', item['id'])['status'] == 'cancelled'
    with pytest.raises(ToolError):
        store.claim(item['id'])


@pytest.mark.parametrize('cancel_first', [True, False])
def test_finish_cancel_fenced_by_transaction(tasks, cancel_first):
    store, a, _ = tasks
    item, _ = store.create_or_get(a, 'ecommerce-v0', body())
    claimed = store.claim(item['id'])
    if cancel_first:
        store.request_cancel(a, 'ecommerce-v0', item['id'])
        with pytest.raises(ToolError):
            store.check_lease(claimed)
    store.finish_if_owner_version(claimed, {'state': 'success'})
    result = store.request_cancel(a, 'ecommerce-v0', item['id'])
    assert result['status'] == ('cancelled' if cancel_first else 'success')
    with pytest.raises(ToolError):
        store.finish_if_owner_version(claimed, {'state': 'success'})
    assert store.request_cancel(a, 'ecommerce-v0', item['id']) == result


def test_graph_checks_between_model_and_tool_nodes():
    from tests.backend.ecommerce.test_v1_agents import Client, Executor, answers
    from tests.backend.ecommerce.test_v1_acceptance import _state
    from data_formulator.ecommerce.v1_agents import invoke_v1_agent_graph
    client, executor = Client(answers()), Executor()
    def checkpoint():
        if len(client.messages) >= 2:
            raise ToolError('INTERRUPTED', 'cancel requested')
    response = invoke_v1_agent_graph(_state('分析2018年1月销售额', 'cancel-graph-001'), executor,
                                     client=client, checkpoint=checkpoint)
    assert response['status'] == 'failed'
    assert response['error']['code'] == 'INTERRUPTED'
    assert len(client.messages) == 2 and executor.calls == []


@pytest.mark.parametrize('target', ['model', 'tool'])
def test_cancel_actual_process_after_dispatch_no_late_work(monkeypatch, tmp_path, target):
    import subprocess
    import sys
    import time
    from types import SimpleNamespace
    from data_formulator.ecommerce import executor, model_transport
    from data_formulator.ecommerce.contracts import parse_request
    from tests.backend.ecommerce.test_multiuser_execution import request_payload
    started, late = tmp_path / 'started', tmp_path / 'late'
    original = subprocess.Popen
    processes = []
    script = ('import sys,time,pathlib;sys.stdin.read();'
              f'pathlib.Path({str(started)!r}).write_text("started");time.sleep(30);'
              f'pathlib.Path({str(late)!r}).write_text("late")')
    def launch(command, **kwargs):
        process = original([sys.executable, '-I', '-c', script], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', launch)
    def checkpoint():
        if started.exists():
            raise ToolError('INTERRUPTED', 'cancelled')
    begin = time.monotonic()
    if target == 'model':
        client = SimpleNamespace(endpoint='openai', model='qwen-flash', deadline=time.monotonic()+10,
                                 checkpoint=checkpoint)
        with pytest.raises(ToolError) as error:
            model_transport.dispatch(client, messages=[], stream=False, params={})
        assert error.value.code == 'INTERRUPTED'
    else:
        result = executor.run_worker(parse_request(request_payload()), {}, checkpoint=checkpoint)
        assert result['state'] == 'failed'
    assert started.exists() and not late.exists()
    assert processes[0].poll() is not None
    assert time.monotonic() - begin < 5
