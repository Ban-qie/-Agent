from pathlib import Path

from data_formulator.ecommerce import v1_service
from data_formulator.ecommerce.contracts import ToolError


def test_v1_service_maps_graph_success_to_api_response(monkeypatch, tmp_path):
    captured = {}

    def fake_graph(state, executor, identity):
        captured.update(state=state, identity=identity)
        return {
            "status": "success", "normalized_conditions": {"operation": "summarize"},
            "verified_result": {"state": "success", "values": {"order_count": 2}},
            "explanation": {"summary": "ok"}, "chart_spec": {"type": "table"},
            "trace": [{"stage": "chart_planner", "status": "success"}],
        }

    monkeypatch.setattr(v1_service, "invoke_v1_business_graph", fake_graph)
    response = v1_service.analyze_v1(
        {"request_id": "v1-api-001", "user_question": "分析2018年1月销售额"},
        "local:test", Path(tmp_path),
    )
    assert response["state"] == "success"
    assert response["result"]["values"]["order_count"] == 2
    assert captured["state"]["run_id"] == "v1-api-001"
    assert captured["identity"] == "local:test"


def test_v1_service_maps_clarification_without_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(v1_service, "invoke_v1_business_graph", lambda *args: {
        "status": "waiting_clarification", "error": {"message": "请明确期间"}, "trace": []
    })
    response = v1_service.analyze_v1(
        {"request_id": "v1-api-002", "user_question": "最近两个完整月销售额"},
        "local:test", Path(tmp_path),
    )
    assert response == {"state": "clarification_required", "question": "请明确期间",
                        "executed": False, "trace": []}


def test_v1_service_rejects_extra_request_fields(tmp_path):
    try:
        v1_service.analyze_v1({"request_id": "v1-api-003", "user_question": "x", "sql": "select 1"},
                              "local:test", Path(tmp_path))
    except ToolError as exc:
        assert exc.code == "INVALID_REQUEST"
    else:
        raise AssertionError("extra fields were accepted")
