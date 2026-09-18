"""HTTP adapter for the explicitly selected V1 graph path."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from data_formulator.ecommerce.contracts import ToolError, error_result
from data_formulator.ecommerce.executor import MetricExecutor, SNAPSHOT_ID
from data_formulator.ecommerce.metrics import METRIC_VERSION
from data_formulator.ecommerce.v1_graph import invoke_v1_business_graph


def analyze_v1(body: Any, identity: str, audit_directory: Path, workspace=None) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"request_id", "user_question", "parent_node_id"}:
        raise ToolError("INVALID_REQUEST", "Only request_id, user_question and parent_node_id are accepted")
    request_id = body["request_id"]
    question = body["user_question"]
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
        raise ToolError("INVALID_REQUEST", "Invalid request_id")
    if not isinstance(question, str) or len(question.encode("utf-8")) > 2048:
        raise ToolError("INVALID_REQUEST", "Invalid user_question")
    parent_node_id = body.get("parent_node_id")
    inherited = workspace.parent_conditions(parent_node_id) if parent_node_id and workspace is not None else None
    state = {
        "run_id": request_id,
        "workspace_id": "ecommerce-v0",
        "snapshot_id": SNAPSHOT_ID,
        "metric_version": METRIC_VERSION,
        "node_id": request_id,
        "user_question": question,
        "parent_node_id": parent_node_id,
        "normalized_conditions": inherited,
        "trace": [],
    }
    if workspace is not None:
        workspace.save_run(node_id=request_id, parent_node_id=parent_node_id, question=question,
                           conditions=dict(inherited or {}), status="running")
    result = invoke_v1_business_graph(
        state, MetricExecutor(Path(audit_directory) / "execution-audit.json"), identity
    )
    status = result.get("status", "failed")
    if status == "waiting_clarification":
        response = {"state": "clarification_required", "question": (result.get("error") or {}).get("message"),
                "executed": False, "trace": result.get("trace", [])}
        if workspace is not None:
            workspace.save_run(node_id=request_id, parent_node_id=parent_node_id, question=question,
                               conditions=result.get("normalized_conditions") or {}, status="waiting_clarification",
                               result=response, error=result.get("error"))
        return response
    response: dict[str, Any] = {
        "state": status,
        "executed": status in {"success", "empty_result", "partial"},
        "conditions": result.get("normalized_conditions"),
        "result": result.get("verified_result"),
        "explanation": result.get("explanation"),
        "chart_spec": result.get("chart_spec"),
        "trace": result.get("trace", []),
    }
    if result.get("error"):
        response["error"] = result["error"]
    if workspace is not None:
        workspace.save_run(node_id=request_id, parent_node_id=parent_node_id, question=question,
                           conditions=result.get("normalized_conditions") or {}, status=status,
                           result=response, chart_spec=result.get("chart_spec"), error=result.get("error"))
    return response
