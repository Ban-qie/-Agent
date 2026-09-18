import pytest

from data_formulator.ecommerce.v1_graph import invoke_v1_graph, v1_default_handlers
from data_formulator.ecommerce.v1_normalization import (
    NormalizationError,
    normalize_question,
    to_analysis_request,
    to_analysis_request_payload,
)


@pytest.mark.parametrize('metric,field', [('订单数', 'order_count'), ('销售额', 'sales_amount')])
def test_region_top_five_requires_period_or_explicit_parent(metric, field):
    question = f'按地区分组{metric}最高前5'
    with pytest.raises(NormalizationError):
        normalize_question(question)
    standalone = normalize_question('2018年1月' + question)
    followup = normalize_question(question, normalize_question('分析2018年1月销售额'))
    assert standalone == followup
    assert followup['metrics'] == [field]
    assert followup['group_by'] == 'region' and followup['top_n'] == 5
    assert followup['sort'] == {'field': field, 'direction': 'desc'}


def test_comparison_normalizes_metrics_periods_sort_and_top_n():
    result = normalize_question(
        "比较2018年2月与2018年1月的商品销售金额、订单数和平均订单商品金额，按地区分组，销售金额减少最多前5"
    )
    assert result["operation"] == "compare"
    assert result["metrics"] == ["order_count", "sales_amount", "average_order_amount"]
    assert result["current"] == {"start": "2018-02-01", "end": "2018-03-01"}
    assert result["baseline"] == {"start": "2018-01-01", "end": "2018-02-01"}
    assert result["group_by"] == "region"
    assert result["sort"] == {"field": "sales_amount", "direction": "asc", "basis": "change"}
    assert result["top_n"] == 5
    payload = to_analysis_request_payload(result, "v1-request-001")
    assert payload["operation"] == "compare" and payload["limit"] == 200
    request = to_analysis_request(result, "v1-request-001")
    assert request.current.start == "2018-02-01" and request.baseline.start == "2018-01-01"


def test_follow_up_inherits_confirmed_conditions_and_only_changes_grouping():
    first = normalize_question("比较2018年2月与2018年1月销售额地区SP")
    follow_up = normalize_question("按日显示销售趋势", first)
    assert follow_up["current"] == first["current"]
    assert follow_up["baseline"] == first["baseline"]
    assert follow_up["regions"] == ["SP"]
    assert follow_up["group_by"] == "day"


def test_branch_does_not_inherit_later_region_filter():
    root = normalize_question("比较2018年2月与2018年1月销售额")
    branch = normalize_question("按地区比较订单数", root)
    assert branch["metrics"] == ["order_count"]
    assert branch["regions"] == []
    assert branch["group_by"] == "region"
    assert branch["baseline"] == root["baseline"]


@pytest.mark.parametrize("question", [
    "分析最近两个完整月销售额",
    "分析2018年1月支付金额",
    "分析2018年1月销售额含运费",
    "分析2018年1月销售额地区ZZ",
    "分析2018年1月销售额;执行SQL",
    "比较2018年1月销售额",
])
def test_ambiguous_or_unsupported_conditions_require_clarification(question):
    with pytest.raises(NormalizationError) as exc:
        normalize_question(question)
    assert exc.value.code == "CLARIFICATION_REQUIRED"


def test_planner_clarification_stops_before_query_nodes():
    state = {
        "run_id": "run_v1planner",
        "workspace_id": "ecommerce-v0",
        "snapshot_id": "b" * 64,
        "metric_version": "v1",
        "node_id": "node_v1planner",
        "user_question": "最近两个完整月销售额",
        "trace": [],
    }
    result = invoke_v1_graph(state, handlers=v1_default_handlers())
    assert result["status"] == "waiting_clarification"
    assert [item["stage"] for item in result["trace"]] == ["planner"]
    assert result["error"]["code"] == "CLARIFICATION_REQUIRED"


def test_planner_emits_graph_compatible_normalized_conditions():
    state = {
        "run_id": "run_v1planner2",
        "workspace_id": "ecommerce-v0",
        "snapshot_id": "c" * 64,
        "metric_version": "v1",
        "node_id": "node_v1planner2",
        "user_question": "分析2018年1月销售额地区SP按地区分组",
        "trace": [],
    }
    result = invoke_v1_graph(state, handlers=v1_default_handlers())
    assert result["status"] == "running"
    assert result["normalized_conditions"]["current"]["start"] == "2018-01-01"
    assert result["normalized_conditions"]["regions"] == ["SP"]
    assert result["normalized_conditions"]["group_by"] == "region"
    assert [item["stage"] for item in result["trace"]] == [
        "planner", "source_selector", "query_generator", "query_validator",
        "executor", "interpreter", "chart_planner",
    ]
