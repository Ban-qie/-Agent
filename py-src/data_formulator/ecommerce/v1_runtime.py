"""Bounded business handlers for the V1 LangGraph.

Every handler receives and returns structured state.  Query construction uses
the existing strict ``AnalysisRequest`` contract; execution is delegated to
the already restricted V0 metric executor and never to model generated code.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from data_formulator.ecommerce.contracts import ToolError, parse_request
from data_formulator.ecommerce.v1_normalization import (
    to_analysis_request,
    to_analysis_request_payload,
)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "failed", "error": {"code": code, "message": message}}


def _conditions(state: Mapping[str, Any]) -> Mapping[str, Any]:
    value = state.get("normalized_conditions")
    if not isinstance(value, Mapping) or value.get("status") == "clarification_required":
        raise ToolError("CLARIFICATION_REQUIRED", "Analysis conditions require clarification")
    return value


def source_selector(state: Mapping[str, Any]) -> dict[str, Any]:
    """Select the one confirmed order-grain source for V1."""
    _conditions(state)
    return {"selected_sources": ["orders"],
            "plan": {**dict(state.get("plan") or {}), "source": "orders", "grain": "one row per order"}}


def query_generator(state: Mapping[str, Any]) -> dict[str, Any]:
    conditions = _conditions(state)
    try:
        payload = to_analysis_request_payload(
            conditions, str(state["run_id"]), str(state["snapshot_id"]), str(state["metric_version"])
        )
    except (KeyError, TypeError, ToolError) as exc:
        return _error("INVALID_REQUEST", str(exc))
    # Keep V1-only presentation requirements alongside the strict executable
    # payload; the executor will receive only the payload after validation.
    return {"query": {"request": payload,
                       "metrics": list(conditions.get("metrics", [])),
                       "sort": deepcopy(conditions.get("sort")),
                       "top_n": conditions.get("top_n")}}


def query_validator(state: Mapping[str, Any]) -> dict[str, Any]:
    query = state.get("query")
    conditions = _conditions(state)
    if not isinstance(query, Mapping) or not isinstance(query.get("request"), Mapping):
        return _error("INVALID_QUERY", "Structured query is missing")
    try:
        request = parse_request(query["request"])
        expected = to_analysis_request(
            conditions, str(state["run_id"]), str(state["snapshot_id"]), str(state["metric_version"])
        )
        if request != expected:
            return _error("CONDITION_MISMATCH", "Query conditions differ from normalized conditions")
        if tuple(state.get("selected_sources", ())) != ("orders",):
            return _error("SOURCE_NOT_ALLOWED", "Only the confirmed orders source is allowed")
    except (KeyError, TypeError, ToolError):
        return _error("INVALID_QUERY", "Query failed deterministic validation")
    return {"query": {**dict(query), "validated": True}}


def executor_handler(executor, identity: str):
    def execute(state: Mapping[str, Any]) -> dict[str, Any]:
        query = state.get("query")
        if not isinstance(query, Mapping) or query.get("validated") is not True:
            return _error("QUERY_NOT_VALIDATED", "Query must pass validation before execution")
        try:
            request = to_analysis_request(
                _conditions(state), str(state["run_id"]), str(state["snapshot_id"]), str(state["metric_version"])
            )
            result = executor.execute(identity, request)
        except ToolError as exc:
            return _error(exc.code, exc.message)
        except Exception:
            return _error("EXECUTION_FAILED", "Restricted metric tool failed")
        state_name = result.get("state") if isinstance(result, Mapping) else None
        if state_name == "failed":
            return {"status": "failed", "verified_result": dict(result),
                    "error": result.get("error") or {"code": "EXECUTION_FAILED", "message": "Metric execution failed"}}
        # outside_coverage is a valid executor result but is represented as a
        # legal V1 empty terminal with the original state preserved.
        status = "empty_result" if state_name in {"empty_result", "outside_coverage"} else state_name
        if status not in {"success", "empty_result", "partial"}:
            return _error("INVALID_RESULT", "Metric tool returned an unsupported result state")
        return {"status": status, "verified_result": dict(result)}
    return execute


def interpreter(state: Mapping[str, Any]) -> dict[str, Any]:
    result = state.get("verified_result")
    if not isinstance(result, Mapping):
        return _error("MISSING_RESULT", "No verified result is available for interpretation")
    status = state.get("status")
    messages = {
        "success": "已按确认的指标、期间和筛选条件完成计算。",
        "empty_result": "没有符合条件的记录；这不等同于真实业务为零。",
        "partial": "部分结果已验证，未生成的部分保持明确标记。",
    }
    summary = messages.get(status, "结果状态需要复核")
    if result.get("state") == "outside_coverage":
        summary = "请求期间超出快照观测范围，未裁剪日期或放宽条件。"
    return {"explanation": {"summary": summary,
                             "status": status,
                             "limitations": ["仅统计已交付订单的商品金额，不含运费。",
                                             "结果来自固定快照；不推断完整业务覆盖或因果原因。"]}}


def chart_planner(state: Mapping[str, Any]) -> dict[str, Any]:
    result = state.get("verified_result")
    conditions = state.get("normalized_conditions") or {}
    if not isinstance(result, Mapping) or result.get("state") in {"failed", "outside_coverage"}:
        return {"chart_spec": {"type": "table", "reason": "no_chart_for_unavailable_result"}}
    group_by = conditions.get("group_by")
    chart_type = "line" if group_by in {"day", "month"} else "bar" if group_by == "region" else "table"
    return {"chart_spec": {"type": chart_type, "dimension": group_by,
                            "measures": list(conditions.get("metrics", [])),
                            "source": "verified_result"}}


def business_handlers(executor, identity: str = "local:v1") -> dict[str, Any]:
    """Create the complete deterministic handler set for one V1 run."""
    return {
        "source_selector": source_selector,
        "query_generator": query_generator,
        "query_validator": query_validator,
        "executor": executor_handler(executor, identity),
        "interpreter": interpreter,
        "chart_planner": chart_planner,
    }
