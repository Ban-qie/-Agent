"""Strict request/result boundary shared by HTTP and the future Analyst skill."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from data_formulator.ecommerce.metrics import METRIC_VERSION, ConditionError, Period, normalize_regions


class ToolError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class StorageUnavailable(Exception):
    """Sanitized storage failure shared by SQLite and PostgreSQL adapters."""


@dataclass(frozen=True)
class AnalysisRequest:
    request_id: str
    snapshot_id: str
    metric_version: str
    operation: str
    current: Period
    baseline: Period | None = None
    regions: tuple = ()
    group_by: str | None = None
    limit: int = 100

    def payload(self):
        return asdict(self)

    def fingerprint(self):
        payload = self.payload()
        payload.pop("request_id")
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def parse_request(payload):
    allowed = {"request_id", "snapshot_id", "metric_version", "operation", "current", "baseline", "regions", "group_by", "limit"}
    required = {"request_id", "snapshot_id", "metric_version", "operation", "current"}
    try:
        if not isinstance(payload, dict) or set(payload) - allowed or not required <= set(payload):
            raise ValueError
        if not isinstance(payload["request_id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", payload["request_id"]):
            raise ValueError
        if not isinstance(payload["snapshot_id"], str) or not re.fullmatch(r"[0-9a-f]{64}", payload["snapshot_id"]):
            raise ValueError
        if payload["metric_version"] != METRIC_VERSION or payload["operation"] not in ("summarize", "compare"):
            raise ValueError
        def period(value):
            if not isinstance(value, dict) or set(value) != {"start", "end"}:
                raise ValueError
            return Period(**value)
        current = period(payload["current"])
        baseline = period(payload["baseline"]) if payload.get("baseline") is not None else None
        if (payload["operation"] == "compare") != (baseline is not None):
            raise ValueError
        group = payload.get("group_by")
        if group not in (None, "region", "day", "month"):
            raise ValueError
        limit = payload.get("limit", 100)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError
        return AnalysisRequest(payload["request_id"], payload["snapshot_id"], payload["metric_version"],
                               payload["operation"], current, baseline, normalize_regions(payload.get("regions")), group, limit)
    except (KeyError, TypeError, ValueError, ConditionError):
        raise ToolError("INVALID_REQUEST", "Invalid or unsupported conditions; no conditions were changed") from None


def error_result(code, message):
    return {"state": "failed", "error": {"code": code, "message": message}, "retryable": False}


class BoundMetricTool:
    """Bind a tool to already confirmed conditions; no model-driven widening."""
    def __init__(self, executor, identity, request):
        self.executor, self.identity = executor, identity
        self.request = parse_request(request.payload())

    def call(self, tool_name, arguments):
        if tool_name != "query_metrics":
            return error_result("TOOL_NOT_ALLOWED", "Only query_metrics is enabled")
        try:
            candidate = parse_request(arguments)
            if candidate != self.request:
                raise ToolError("CONDITION_MISMATCH", "Tool arguments differ from confirmed analysis conditions")
            return self.executor.execute(self.identity, candidate)
        except ToolError as exc:
            return error_result(exc.code, exc.message)
