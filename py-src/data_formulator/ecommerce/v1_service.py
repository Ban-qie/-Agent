"""HTTP adapter for the explicitly selected V1 graph path."""
from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext
import re
from typing import Any

from data_formulator.ecommerce.contracts import ToolError, error_result
from data_formulator.ecommerce.executor import MetricExecutor, SNAPSHOT_ID
from data_formulator.ecommerce.metrics import METRIC_VERSION
from data_formulator.ecommerce.v1_agents import invoke_v1_agent_graph as invoke_v1_business_graph


def validate_analyze_request(body: Any) -> None:
    if not isinstance(body, dict) or set(body) - {"request_id", "user_question", "parent_node_id"}:
        raise ToolError("INVALID_REQUEST", "Only request_id, user_question and parent_node_id are accepted")
    request_id = body.get("request_id")
    question = body.get("user_question")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
        raise ToolError("INVALID_REQUEST", "Invalid request_id")
    try:
        valid_question = isinstance(question, str) and bool(question.strip()) and len(question.encode("utf-8")) <= 2048
    except UnicodeError:
        valid_question = False
    if not valid_question:
        raise ToolError("INVALID_REQUEST", "Invalid user_question")
    parent_node_id = body.get("parent_node_id")
    if parent_node_id is not None and (not isinstance(parent_node_id, str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", parent_node_id) or parent_node_id == request_id):
        raise ToolError("INVALID_REQUEST", "Invalid parent_node_id")


def analyze_v1(body: Any, identity: str, audit_directory: Path, workspace=None, *, executor=None, client=None, checkpoint=None) -> dict[str, Any]:
    validate_analyze_request(body)
    request_id = body['request_id']
    if body.get('parent_node_id') and workspace is None:
        raise ToolError("PARENT_NOT_FOUND", "Parent requires a saved workspace")
    if workspace is not None:
        workspace.read()  # Resolve abandoned runs before acquiring this run's lease.
    with workspace.task_lease(request_id) if workspace is not None else nullcontext():
        return _run(body, identity, audit_directory, workspace, executor=executor, client=client, checkpoint=checkpoint)


def _run(body, identity, audit_directory, workspace, *, executor=None, client=None, checkpoint=None):
    request_id, question = body["request_id"], body["user_question"]
    parent_node_id = body.get("parent_node_id")
    inherited = workspace.parent_conditions(parent_node_id) if parent_node_id and workspace is not None else None
    if workspace is not None:
        existing = workspace.get_node(request_id)
        if existing is not None:
            if existing["question"] != question or existing.get("parent_node_id") != parent_node_id:
                raise ToolError("REQUEST_CONFLICT", "V1 request ID belongs to another question or parent")
            if existing["status"] in {"success", "empty_result", "partial", "failed", "waiting_clarification", "interrupted"}:
                saved = existing.get("result") or {}
                if saved:
                    return saved
            if existing["status"] == "running":
                raise ToolError("BUSY", "V1 request is already running")
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
    try:
        selected_executor = executor if executor is not None else MetricExecutor(Path(audit_directory) / 'execution-audit.json')
        options = {}
        if client is not None:
            options['client'] = client
        if checkpoint is not None:
            options['checkpoint'] = checkpoint
        result = invoke_v1_business_graph(state, selected_executor, identity, **options)
    except Exception as exc:
        result = {"status": "failed", "normalized_conditions": inherited,
                  "error": {"code": exc.code if isinstance(exc, ToolError) else "ANALYSIS_FAILED", "message": "V1 analysis could not complete"}}
    status = result.get("status", "failed")
    if status == "waiting_clarification":
        response = {"state": "clarification_required", "question": (result.get("error") or {}).get("message"),
                "executed": False, "trace": result.get("trace", [])}
        if result.get('budget'):
            response['budget'] = result['budget']
            response['collaboration'] = (result.get('plan') or {}).get('collaboration', {})
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
        "collaboration": result.get('plan', {}).get('collaboration', {}) if result.get('plan') else {},
        "budget": result.get('budget', {}),
    }
    if result.get("error"):
        response["error"] = result["error"]
    if workspace is not None:
        try:
            workspace.save_run(node_id=request_id, parent_node_id=parent_node_id, question=question,
                               conditions=result.get("normalized_conditions") or {}, status=status,
                               result=response, chart_spec=result.get("chart_spec"), error=result.get("error"))
        except OSError:
            # The caller may still inspect completed calculations. On restart the
            # durable running marker becomes interrupted, never automatically rerun.
            response.update(state='failed', error={'code': 'WORKSPACE_UNAVAILABLE',
                'message': 'Result could not be saved; automatic replay is disabled'})
    return response
