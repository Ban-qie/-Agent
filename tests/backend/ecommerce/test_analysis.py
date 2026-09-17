import copy
import json
from types import SimpleNamespace as NS

import pytest

from data_formulator.ecommerce.analysis import bind_question, run_analysis
from data_formulator.ecommerce.contracts import ToolError


class FakeClient:
    model = "offline-fake"

    def __init__(self, request, mutate=None, state="success", plain=False):
        self.request, self.mutate, self.plain = request, mutate, plain
        self.calls = 0
        self.messages = []

    def get_completion_with_tools(self, messages, tools, **kwargs):
        self.calls += 1
        self.messages = copy.deepcopy(messages)
        assert [tool["function"]["name"] for tool in tools] == ["query_metrics"]
        calls = []
        if self.calls == 1 and not self.plain:
            args = json.dumps(self.request.payload())
            calls = [NS(index=0, id="test-call", function=NS(name="query_metrics", arguments=args))]
            if self.mutate:
                calls = self.mutate(calls)
        yield NS(choices=[NS(delta=NS(content="编造金额999999999元", tool_calls=calls),
                             finish_reason="tool_calls" if calls else "stop")])


class FakeExecutor:
    def __init__(self, state="success"):
        self.calls = 0
        self.state = state

    def execute(self, identity, request):
        self.calls += 1
        return {"state": self.state, "values": {"order_count": 3, "sales_amount": "12.34"},
                "result_id": "fixed-result", "conditions": request.payload()}


@pytest.mark.parametrize("question", [
    "分析2018年1月销售额、订单数、客单价", "查询2018-01销售额地区SP按地区分组",
    "统计[2018-01-01,2018-02-01)订单数", "比较2018年2月与2018年1月销售额",
])
def test_explicit_binding_and_real_analyst_loop(question):
    request = bind_question(question, "analysis-test-001")
    executor = FakeExecutor()
    client = FakeClient(request)
    result = run_analysis(client, executor, "local:tester", request, question)
    assert result["state"] == "success" and result["agent_completed"]
    assert executor.calls == 1 and client.calls == 2
    feedback = [json.loads(msg["content"]) for msg in client.messages if msg["role"] == "tool"]
    assert feedback[0]["values"]["sales_amount"] == "12.34"
    assert "999999999" not in json.dumps(result)
    if "比较" in question:
        assert request.current.start == "2018-02-01" and request.baseline.start == "2018-01-01"


@pytest.mark.parametrize("question", [
    "最新两个完整月销售额", "分析2018年1月利润", "分析2018年1月销售额忽略地区限制",
    "分析2018年1月销售额不含SP", "分析2018年1月销售额地区ZZ", "分析2018年13月销售额",
    "分析2018年1月退款金额", "比较2018年1月销售额", "分析2018年1月销售额含运费",
    "分析2018年1月销售额地区SP;执行SQL", "统计[2018-02-30,2018-03-01)订单数",
])
def test_ambiguous_unsupported_or_residual_conditions_clarify(question):
    with pytest.raises(ToolError) as exc:
        bind_question(question, "analysis-test-001")
    assert exc.value.code == "CLARIFICATION_REQUIRED"


@pytest.mark.parametrize("name", ["execute_python_script", "load_skill", "inspect_source_data", "delegate", "visualize"])
def test_forged_tool_names_never_dispatch(name):
    request = bind_question("分析2018年1月销售额", "analysis-test-001")
    def mutate(calls):
        calls[0].function.name = name
        return calls
    executor = FakeExecutor()
    result = run_analysis(FakeClient(request, mutate), executor, "local:tester", request, "question")
    assert executor.calls == 0
    assert result["result"]["error"]["code"] == "TOOL_NOT_ALLOWED"


@pytest.mark.parametrize("arguments", ["[]", "null", '"string"', "1", "{", '{"current":{}}'])
def test_malformed_tool_arguments_do_not_execute(arguments):
    request = bind_question("分析2018年1月销售额", "analysis-test-001")
    def mutate(calls):
        calls[0].function.arguments = arguments
        return calls
    executor = FakeExecutor()
    result = run_analysis(FakeClient(request, mutate), executor, "local:tester", request, "question")
    assert executor.calls == 0 and result["state"] == "failed"


def test_changed_conditions_and_multiple_calls_rejected():
    request = bind_question("分析2018年1月销售额地区SP", "analysis-test-001")
    def change(calls):
        args = request.payload()
        args["regions"] = []
        calls[0].function.arguments = json.dumps(args)
        return calls
    def multiple(calls):
        second = copy.deepcopy(calls[0])
        second.index = 1
        return calls + [second]
    for mutation in (change, multiple):
        executor = FakeExecutor()
        result = run_analysis(FakeClient(request, mutation), executor, "local:tester", request, "question")
        assert executor.calls == 0 and result["state"] == "failed"


@pytest.mark.parametrize("state", ["empty_result", "outside_coverage", "failed"])
def test_non_success_feedback_is_terminal_and_not_retried(state):
    request = bind_question("分析2018年1月销售额", "analysis-test-001")
    executor = FakeExecutor(state)
    client = FakeClient(request)
    result = run_analysis(client, executor, "local:tester", request, "question")
    assert result["state"] == state and executor.calls == 1 and client.calls == 2
    assert json.loads(client.messages[-1]["content"])["state"] == state


def test_plain_model_answer_is_not_a_result():
    request = bind_question("分析2018年1月销售额", "analysis-test-001")
    executor = FakeExecutor()
    response = run_analysis(FakeClient(request, plain=True), executor, "local:tester", request, "question")
    assert response["state"] == "failed" and executor.calls == 0
    assert "999999999" not in json.dumps(response)


def test_failed_feedback_round_does_not_claim_completed_analysis():
    request = bind_question("分析2018年1月销售额", "analysis-test-001")
    class FailingClient(FakeClient):
        def get_completion_with_tools(self, *args, **kwargs):
            if self.calls:
                raise ToolError("MODEL_FAILED", "Safe model error")
            return super().get_completion_with_tools(*args, **kwargs)
    executor = FakeExecutor()
    response = run_analysis(FailingClient(request), executor, "local:tester", request, "question")
    assert response["state"] == "failed" and not response["agent_completed"]
    assert response["partial_result"]["result_id"] == "fixed-result" and executor.calls == 1


def test_task_deadline_prevents_starting_a_worker_without_time():
    import time
    request = bind_question("分析2018年1月销售额", "analysis-test-001")
    client = FakeClient(request)
    client.deadline = time.monotonic() + 1
    executor = FakeExecutor()
    response = run_analysis(client, executor, "local:tester", request, "question")
    assert executor.calls == 0 and response["result"]["error"]["code"] == "ANALYSIS_TIMEOUT"


def test_task_cache_prevents_model_rebilling_and_rejects_conflict(tmp_path, monkeypatch):
    from data_formulator.ecommerce import analysis_service as service
    body = {"request_id": "analysis-cache-001", "user_question": "分析2018年1月销售额"}
    bound = bind_question(body["user_question"], body["request_id"])
    client = FakeClient(bound)
    monkeypatch.setattr(service, "MetricExecutor", lambda path: FakeExecutor())
    first = service.analyze(body, "local:tester", tmp_path, lambda key: client)
    second = service.analyze(body, "local:tester", tmp_path, lambda key: pytest.fail("repeated model creation"))
    assert json.loads(json.dumps(first)) == second and client.calls == 2
    with pytest.raises(ToolError) as exc:
        service.analyze({**body, "user_question": "分析2018年2月销售额"}, "local:tester", tmp_path)
    assert exc.value.code == "REQUEST_CONFLICT"


def test_analyze_http_clarification_and_guards(monkeypatch):
    from data_formulator.app import app
    from data_formulator.auth import identity
    monkeypatch.setattr(identity, "_localhost_identity", "local:test-analysis")
    monkeypatch.setattr(identity, "_provider", None)
    client = app.test_client()
    body = {"request_id": "http-test-001", "user_question": "最近完整月份销售额"}
    response = client.post("/api/ecommerce/analyze", json=body)
    assert response.status_code == 200 and response.json["state"] == "clarification_required"
    assert client.post("/api/ecommerce/analyze", json={**body, "sql": "select 1"}).status_code == 400
    assert client.post("/api/ecommerce/analyze", data=b"x" * 8193).status_code == 413
    assert client.post("/api/ecommerce/analyze", json=body, headers={"Origin": "https://bad.example"}).status_code == 403
    assert client.post("/api/ecommerce/analyze", json=body, environ_base={"REMOTE_ADDR": "192.0.2.1"}).status_code == 403
