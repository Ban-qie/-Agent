import json
import time
from types import SimpleNamespace as NS

import pytest

from data_formulator.ecommerce.budget import BudgetClient, UsageLedger, exclusive
from data_formulator.ecommerce.contracts import ToolError


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text(json.dumps([{"attempt": i, "reserved_cny": .01, "state": "response_received"}
                                for i in range(1, 6)]), encoding="utf-8")
    return UsageLedger(path)


def test_history_is_inherited_and_reservation_precedes_dispatch(ledger, monkeypatch):
    from data_formulator.agents.client_utils import Client
    client = BudgetClient("openai", "qwen-flash", "not-real", "https://invalid.example")
    client.ledger, client.task_id = ledger, "task-id"
    def dispatch(self, **kwargs):
        rows = ledger.read()
        assert len(rows) == 6 and rows[-1]["state"] == "reserved"
        assert kwargs["params"]["num_retries"] == kwargs["params"]["max_retries"] == 0
        assert kwargs["params"]["max_tokens"] == 768
        assert "reasoning_effort" not in kwargs["params"]
        return NS(usage=NS(prompt_tokens=100, completion_tokens=10))
    monkeypatch.setattr(Client, "_dispatch", dispatch)
    client.get_completion([{"role": "user", "content": "test"}])
    rows = ledger.read()
    assert len(rows) == 6 and rows[-1]["input_tokens"] == 100
    assert rows[-1]["estimated_cny"] == pytest.approx(.00003)
    assert sum(r["reserved_cny"] for r in rows) == pytest.approx(.07)


def test_project_budget_and_corrupt_missing_history_fail_closed(ledger):
    rows = ledger.read()
    rows[-1]["reserved_cny"] = 9.96
    ledger.path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        ledger.reserve("task")
    assert exc.value.code == "BUDGET_EXHAUSTED"
    ledger.path.write_text("[]", encoding="utf-8")
    with pytest.raises(ToolError):
        ledger.reserve("task")
    with pytest.raises(ToolError):
        UsageLedger(ledger.path.parent / "missing.json").reserve("task")


def test_cross_process_lock_prevents_parallel_reservation(ledger):
    with exclusive(ledger.path):
        with pytest.raises(ToolError) as exc:
            ledger.reserve("task")
    assert exc.value.code == "BUSY" and len(ledger.read()) == 5


def test_failed_dispatch_is_reserved_and_not_implicitly_retried(ledger, monkeypatch):
    from data_formulator.agents.client_utils import Client
    client = BudgetClient("openai", "qwen-flash")
    client.ledger, client.task_id = ledger, "task"
    def fail(*args, **kwargs):
        raise RuntimeError("SECRET reasoning_effort unsupported")
    monkeypatch.setattr(Client, "_dispatch", fail)
    with pytest.raises(ToolError) as exc:
        client.get_completion_with_tools([], [])
    assert "SECRET" not in str(exc.value)
    assert len(ledger.read()) == 6 and ledger.read()[-1]["state"] == "error_or_unknown"


def test_call_token_time_and_ping_limits_do_not_dispatch(ledger, monkeypatch):
    from data_formulator.agents.client_utils import Client
    monkeypatch.setattr(Client, "_dispatch", lambda *a, **k: pytest.fail("network attempted"))
    client = BudgetClient("openai", "qwen-flash")
    client.ledger, client.task_id = ledger, "task"
    with pytest.raises(ToolError):
        client.ping()
    with pytest.raises(ToolError):
        client.get_completion([{"role": "user", "content": "x" * 16385}])
    client.calls = 2
    with pytest.raises(ToolError):
        client.get_completion([])
    client.calls = 0
    client.deadline = time.monotonic() - 1
    with pytest.raises(ToolError):
        client.get_completion([])
    assert len(ledger.read()) == 5


def test_stream_usage_unknown_and_close_accounting(ledger, monkeypatch):
    from data_formulator.agents.client_utils import Client
    class Stream:
        closed = False
        def __iter__(self):
            yield NS(choices=[], usage=None)
        async def aclose(self):
            self.closed = True
    stream = Stream()
    monkeypatch.setattr(Client, "_dispatch", lambda *a, **k: stream)
    client = BudgetClient("openai", "qwen-flash")
    client.ledger, client.task_id = ledger, "task"
    list(client.get_completion_with_tools([], [], stream=True))
    assert stream.closed and ledger.read()[-1]["state"] == "usage_unknown"


def test_running_task_is_not_restarted(tmp_path):
    import hashlib
    from data_formulator.ecommerce.analysis_service import analyze
    body = {"request_id": "interrupted-task", "user_question": "分析2018年1月销售额"}
    key = hashlib.sha256(("local:tester\0" + body["request_id"]).encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    (tmp_path / "analysis-audit.json").write_text(json.dumps({key: {"fingerprint": fingerprint, "status": "running"}}))
    with pytest.raises(ToolError) as exc:
        analyze(body, "local:tester", tmp_path, lambda key: pytest.fail("model restarted"))
    assert exc.value.code == "INTERRUPTED"


def test_team_shares_three_call_limit_without_changing_v0(ledger, monkeypatch):
    from data_formulator.agents.client_utils import Client
    from data_formulator.ecommerce.v1_agents import QwenTeam
    monkeypatch.setattr(Client, '_dispatch', lambda *args, **kwargs:
        NS(usage=NS(prompt_tokens=100, completion_tokens=10),
           choices=[NS(message=NS(content='{}'))]))
    class TeamBudget(BudgetClient):
        max_calls = 3
    client = TeamBudget('openai', 'qwen-flash')
    client.task_id, client.ledger = 'v1-team:test-limit', ledger
    team = QwenTeam('test-limit', client)
    for role in ['planner', 'reviewer', 'interpreter']:
        team.ask(role, 'system', {})
    with pytest.raises(ToolError) as exc:
        team.ask('planner', 'system', {})
    assert exc.value.code == 'CALL_LIMIT'
    assert len(ledger.read()) == 8 and ledger.read()[-1]['stage'] == 'V1-team'
    assert BudgetClient.max_calls == 2
