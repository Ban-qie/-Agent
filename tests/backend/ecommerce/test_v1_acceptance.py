from pathlib import Path

from data_formulator.ecommerce.executor import MetricExecutor, SNAPSHOT_ID
from data_formulator.ecommerce.v1_graph import invoke_v1_business_graph
from data_formulator.ecommerce.v1_normalization import normalize_question, to_analysis_request


class ExecutorAdapter:
    def __init__(self, executor):
        self.executor = executor
        self.calls = 0

    def execute(self, identity, request):
        self.calls += 1
        return self.executor.execute(identity, request)


def _state(question, run_id):
    return {
        "run_id": run_id, "workspace_id": "ecommerce-v0", "snapshot_id": SNAPSHOT_ID,
        "metric_version": "olist-delivered-purchase-item-price-v1", "node_id": run_id,
        "user_question": question, "trace": [],
    }


def test_v1_matches_v0_for_core_comparison_and_region_groups(tmp_path):
    question = "比较2018年2月与2018年1月的商品销售金额、订单数和平均订单商品金额，按地区分组"
    conditions = normalize_question(question)
    v0 = MetricExecutor(Path(tmp_path) / "v0-audit.json")
    expected = v0.execute("local:acceptance-v0", to_analysis_request(conditions, "v0-accept-001"))
    v1 = MetricExecutor(Path(tmp_path) / "v1-audit.json")
    actual = invoke_v1_business_graph(_state(question, "v1-accept-001"), v1, "local:acceptance-v1")
    assert actual["status"] == expected["state"] == "success"
    assert actual["verified_result"]["current"]["values"] == expected["current"]["values"]
    assert actual["verified_result"]["baseline"]["values"] == expected["baseline"]["values"]
    assert actual["verified_result"]["current"]["total_groups"] == expected["current"]["total_groups"]
    assert actual["verified_result"]["baseline"]["total_groups"] == expected["baseline"]["total_groups"]


def test_v1_preserves_empty_and_outside_coverage_states(tmp_path):
    executor = MetricExecutor(Path(tmp_path) / "audit.json")
    empty = invoke_v1_business_graph(_state("分析2016年11月销售额", "v1-accept-002"), executor)
    outside = invoke_v1_business_graph(_state("分析2020年1月销售额", "v1-accept-003"), executor)
    assert empty["status"] == "empty_result"
    assert empty["verified_result"]["state"] == "empty_result"
    assert outside["status"] == "empty_result"
    assert outside["verified_result"]["state"] == "outside_coverage"
    assert "超出快照观测范围" in outside["explanation"]["summary"]


def test_v1_follow_up_and_branch_use_independent_conditions():
    root = normalize_question("比较2018年2月与2018年1月销售额")
    region_follow_up = normalize_question("按地区拆开销售金额减少最多前3", root)
    independent_branch = normalize_question("按地区比较订单数", root)
    assert region_follow_up["regions"] == []
    assert region_follow_up["top_n"] == 3
    assert independent_branch["metrics"] == ["order_count"]
    assert independent_branch["regions"] == []
    assert independent_branch["top_n"] is None
