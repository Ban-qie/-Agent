from data_formulator.ecommerce.v1_graph import invoke_v1_business_graph


def _state(question="分析2018年1月销售额地区SP按地区分组"):
    return {
        "run_id": "run_v1runtime",
        "workspace_id": "ecommerce-v0",
        "snapshot_id": "5" * 64,
        "metric_version": "olist-delivered-purchase-item-price-v1",
        "node_id": "node_v1runtime",
        "user_question": question,
        "trace": [],
    }


class FakeExecutor:
    def __init__(self, state="success"):
        self.calls = 0
        self.state = state

    def execute(self, identity, request):
        self.calls += 1
        return {
            "state": self.state,
            "values": {"order_count": 2, "sales_amount": "12.34", "average_order_amount": "6.17"},
            "groups": [{"key": "SP", "order_count": 2, "sales_amount": "12.34"}],
            "conditions": request.payload(),
        }


def test_business_graph_executes_only_validated_conditions_and_explains_result():
    executor = FakeExecutor()
    result = invoke_v1_business_graph(_state(), executor, "local:test")
    assert result["status"] == "success"
    assert executor.calls == 1
    assert result["selected_sources"] == ("orders",)
    assert result["query"]["validated"] is True
    assert result["verified_result"]["values"]["sales_amount"] == "12.34"
    assert result["explanation"]["status"] == "success"
    assert result["chart_spec"] == {
        "type": "bar", "dimension": "region", "measures": ["sales_amount"],
        "source": "verified_result",
    }


def test_empty_result_is_terminal_and_does_not_get_presented_as_zero():
    executor = FakeExecutor("empty_result")
    result = invoke_v1_business_graph(_state(), executor)
    assert result["status"] == "empty_result"
    assert "不等同于真实业务为零" in result["explanation"]["summary"]
    assert executor.calls == 1


def test_outside_coverage_remains_distinguishable_from_empty_business_data():
    executor = FakeExecutor("outside_coverage")
    result = invoke_v1_business_graph(_state(), executor)
    assert result["status"] == "empty_result"
    assert "超出快照观测范围" in result["explanation"]["summary"]


def test_clarification_stops_before_source_selection_and_execution():
    executor = FakeExecutor()
    result = invoke_v1_business_graph(_state("最近两个完整月销售额"), executor)
    assert result["status"] == "waiting_clarification"
    assert result["trace"] == [{"stage": "planner", "status": "waiting_clarification"}]
    assert executor.calls == 0


def test_query_validator_rejects_condition_tampering_before_executor():
    executor = FakeExecutor()

    from data_formulator.ecommerce.v1_runtime import business_handlers
    from data_formulator.ecommerce.v1_graph import v1_default_handlers, invoke_v1_graph

    base = business_handlers(executor)
    original_generator = base["query_generator"]

    def tamper(state):
        query = dict(original_generator(state)["query"])
        request = dict(query["request"])
        request["regions"] = []
        query["request"] = request
        return {"query": query}

    handlers = {**v1_default_handlers(), **base, "query_generator": tamper}
    result = invoke_v1_graph(_state(), handlers)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "CONDITION_MISMATCH"
    assert executor.calls == 0
