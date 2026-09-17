"""Identity-scoped task audit in the existing local application's data directory."""
import hashlib
import json
from pathlib import Path
import re

from data_formulator.ecommerce.analysis import bind_question, run_analysis
from data_formulator.ecommerce.budget import configured_client, exclusive, save_json
from data_formulator.ecommerce.contracts import ToolError, error_result
from data_formulator.ecommerce.executor import MetricExecutor


def completed_response(body, identity, directory):
    """Read an atomically committed audit response, never execute or clear locks."""
    path = Path(directory) / "analysis-audit.json"
    try:
        if not path.exists():
            return None
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError
        records = json.loads(path.read_text(encoding="utf-8"))
        key = hashlib.sha256((identity + "\0" + body["request_id"]).encode()).hexdigest()
        old = records.get(key)
        if not old:
            return None
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if old["fingerprint"] != fingerprint:
            raise ValueError
        return old["response"] if old["status"] == "completed" else None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ToolError("WORKSPACE_UNAVAILABLE", "Analysis history requires review") from None


def analyze(body, identity, directory, client_factory=configured_client):
    if not isinstance(body, dict) or set(body) != {"request_id", "user_question"}:
        raise ToolError("INVALID_REQUEST", "Only request_id and user_question are accepted")
    if not isinstance(identity, str) or not identity.startswith("local:"):
        raise ToolError("ACCESS_DENIED", "Authenticated local identity required")
    rid = body["request_id"]
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", rid):
        raise ToolError("INVALID_REQUEST", "Invalid request_id")
    bound = bind_question(body["user_question"], rid)
    path = Path(directory) / "analysis-audit.json"
    key = hashlib.sha256((identity + "\0" + rid).encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    with exclusive(path):
        if path.exists() and path.stat().st_size > 20 * 1024 * 1024:
            raise ToolError("RESOURCE_LIMIT", "Analysis audit capacity reached")
        records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        old = records.get(key)
        if old:
            if old["fingerprint"] != fingerprint:
                raise ToolError("REQUEST_CONFLICT", "Request ID belongs to a different question")
            if old["status"] != "completed":
                raise ToolError("INTERRUPTED", "Prior analysis requires interruption review")
            return old["response"]
        if len(records) >= 128:
            raise ToolError("RESOURCE_LIMIT", "Analysis task allowance exhausted")
        records[key] = {"fingerprint": fingerprint, "status": "running"}
        save_json(path, records)
        try:
            client = client_factory(key)
            response = run_analysis(client, MetricExecutor(path.parent / "execution-audit.json"),
                                    identity, bound, body["user_question"])
        except ToolError as exc:
            response = error_result(exc.code, exc.message)
        except Exception:
            response = error_result("ANALYSIS_FAILED", "Restricted analysis failed")
        response["conditions"] = bound.payload()
        records[key].update(status="completed", response=response)
        save_json(path, records)
        return response
