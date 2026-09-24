"""Real disposable subprocesses; no provider credentials or paid requests."""
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from data_formulator.ecommerce import model_transport as transport
from data_formulator.ecommerce.contracts import ToolError


def replace_worker(monkeypatch, script):
    original = subprocess.Popen
    processes = []
    def launch(command, **kwargs):
        process = original([sys.executable, '-I', '-c', script], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(transport.subprocess, 'Popen', launch)
    return processes


def call(seconds=5, stream=False):
    client = SimpleNamespace(endpoint='openai', model='qwen-flash', deadline=time.monotonic() + seconds)
    return transport.dispatch(client, messages=[], stream=stream, params={'api_key': 'fake-only'})


def test_blocked_process_is_killed_and_next_request_works(monkeypatch, tmp_path):
    marker = tmp_path / 'late.txt'
    script = ('import sys,time,pathlib; sys.stdin.read(); time.sleep(30); '
              f'pathlib.Path({str(marker)!r}).write_text("late")')
    processes = replace_worker(monkeypatch, script)
    start = time.monotonic()
    with pytest.raises(ToolError) as exc:
        call(.5)
    assert exc.value.code == 'ANALYSIS_TIMEOUT'
    assert time.monotonic() - start < 3
    assert processes[0].poll() is not None and not marker.exists()
    monkeypatch.undo()
    good = json.dumps({'response': {'choices': [{'message': {'role': 'assistant', 'content': '{}'}}]}})
    processes = replace_worker(monkeypatch, f'import sys; sys.stdin.read(); print({good!r})')
    assert call().choices[0].message.content == '{}'
    assert processes[0].poll() == 0


@pytest.mark.parametrize('script', ['import sys; sys.stdin.read(); sys.exit(7)',
                                    'import sys; sys.stdin.read(); print("{\\\"error\\\":\\\"MODEL_FAILED\\\"}")'])
def test_worker_failure_is_bounded_and_sanitized(monkeypatch, script):
    processes = replace_worker(monkeypatch, script)
    with pytest.raises(ToolError) as exc:
        call()
    assert exc.value.code == 'MODEL_FAILED'
    assert processes[0].poll() is not None


def test_buffered_stream_preserves_usage_and_tool_calls(monkeypatch):
    wire = json.dumps({'chunks': [{'choices': [{'index': 0, 'delta': {'content': 'x'}}]},
        {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}]})
    replace_worker(monkeypatch, f'import sys; sys.stdin.read(); print({wire!r})')
    chunks = list(call(stream=True))
    assert chunks[0].choices[0].delta.content == 'x'
    assert chunks[-1].usage.prompt_tokens == 10


@pytest.mark.parametrize('large', [False, True])
def test_actual_wire_adapter_with_stub_sdk(monkeypatch, large):
    script = '''
from data_formulator.agents.client_utils import Client
from litellm import ModelResponse
from data_formulator.ecommerce.model_worker import main
Client._dispatch = lambda *a, **k: ModelResponse(choices=[{'message': {'role': 'assistant', 'content': 'x' * SIZE}}], usage={'prompt_tokens': 10, 'completion_tokens': 2})
main()
'''.replace('SIZE', '600000' if large else '2')
    processes = replace_worker(monkeypatch, script)
    if large:
        with pytest.raises(ToolError) as exc:
            call(30)
        assert exc.value.code == 'MODEL_FAILED'
    else:
        response = call(30)
        assert response.choices[0].message.content == 'xx'
        assert response.usage.prompt_tokens == 10
    assert processes[0].poll() == 0


def test_worker_failure_logs_only_safe_category(monkeypatch, caplog):
    script = '''
from data_formulator.agents.client_utils import Client
from data_formulator.ecommerce.model_worker import main
Client._dispatch = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("private provider detail"))
main()
'''
    replace_worker(monkeypatch, script)
    with pytest.raises(ToolError) as exc:
        call(30)
    assert exc.value.code == 'MODEL_FAILED'
    assert 'category=timeout' in caplog.text
    assert 'private provider detail' not in caplog.text


@pytest.mark.parametrize(('error_source', 'expected'), [
    ('type("BadRequestError", (Exception,), {"status_code": 400})',
     'category=bad_request status=400'),
    ('type("AuthenticationError", (Exception,), {"status_code": 401})',
     'category=authentication status=401'),
    ('type("PermissionDeniedError", (Exception,), {"status_code": 403})',
     'category=permission status=403'),
    ('type("RateLimitError", (Exception,), {"status_code": 429})',
     'category=rate_limit status=429'),
    ('type("BudgetExceededError", (Exception,), {"status_code": 429, "rate_limit_type": "budget"})',
     'category=quota status=429'),
    ('type("ServiceUnavailableError", (Exception,), {"status_code": 503})',
     'category=server status=503'),
    ('type("APIConnectionError", (Exception,), {})', 'category=connection'),
    ('type("APIResponseValidationError", (Exception,), {"status_code": 500})',
     'category=response_parse status=500'),
])
def test_worker_failure_logs_safe_diagnostic_bucket(monkeypatch, caplog,
                                                    error_source, expected):
    script = f'''
from data_formulator.agents.client_utils import Client
from data_formulator.ecommerce.model_worker import main
Error = {error_source}
Client._dispatch = lambda *a, **k: (_ for _ in ()).throw(Error("private provider detail"))
main()
'''
    replace_worker(monkeypatch, script)
    with pytest.raises(ToolError):
        call(30)
    assert expected in caplog.text
    assert 'private provider detail' not in caplog.text


def test_worker_logs_only_allowlisted_provider_code(monkeypatch, caplog):
    script = '''
from data_formulator.agents.client_utils import Client
from data_formulator.ecommerce.model_worker import main
class Error(Exception):
    status_code = 429
    code = "QuotaExceeded"
    body = {"error": {"code": "secret-account-identifier"}}
Client._dispatch = lambda *a, **k: (_ for _ in ()).throw(Error("private provider detail"))
main()
'''
    replace_worker(monkeypatch, script)
    with pytest.raises(ToolError):
        call(30)
    assert 'category=quota status=429 provider_code=QuotaExceeded' in caplog.text
    assert 'secret-account-identifier' not in caplog.text
    assert 'private provider detail' not in caplog.text


def test_killed_transport_keeps_parent_reservation(monkeypatch, tmp_path):
    from data_formulator.ecommerce.budget import BudgetClient, UsageLedger
    path = tmp_path / 'usage.json'
    path.write_text(json.dumps([{'attempt': i, 'reserved_cny': .02} for i in range(1, 6)]))
    processes = replace_worker(monkeypatch, 'import sys,time; sys.stdin.read(); time.sleep(30)')
    client = BudgetClient('openai', 'qwen-flash')
    client.ledger, client.task_id = UsageLedger(path), 'v2-hard-stop'
    client.deadline = time.monotonic() + .5
    with pytest.raises(ToolError) as exc:
        client.get_completion([])
    assert exc.value.code == 'ANALYSIS_TIMEOUT'
    assert processes[0].poll() is not None
    rows = UsageLedger(path).read()
    assert len(rows) == 6 and rows[-1]['state'] == 'error_or_unknown'
    assert rows[-1]['reserved_cny'] == .02
    assert not path.with_suffix('.lock').exists()
    with pytest.raises(ToolError):
        client.get_completion([])
    assert len(UsageLedger(path).read()) == 6
