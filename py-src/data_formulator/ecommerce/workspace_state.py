"""Server-owned analysis history in the upstream local WorkspaceManager.

OS leases survive neither process exit nor crashes. Reads never restart work.
Legacy execution and budget locks are deliberately never removed here.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path

from data_formulator.ecommerce.contracts import ToolError, error_result
from data_formulator.ecommerce.analysis import bind_question
from data_formulator.ecommerce.executor import SNAPSHOT_ID
from data_formulator.ecommerce.metrics import METRIC_VERSION

WORKSPACE_ID = "ecommerce-v0"
CHART = {"version": 1, "kind": "sales_bar", "measure": "sales_amount",
         "category": "label", "series": "period"}
STATES = {"running", "success", "empty_result", "outside_coverage", "clarification_required", "failed", "interrupted"}


@contextmanager
def lease(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ToolError("BUSY", "Workspace operation is active") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class AnalysisWorkspace:
    def __init__(self, manager, identity, audit_directory=None):
        if not isinstance(identity, str) or not identity.startswith("local:"):
            raise ToolError("ACCESS_DENIED", "Local identity required")
        self.manager, self.identity = manager, identity
        self.path = manager.get_workspace_path(WORKSPACE_ID)
        self.audit_directory = Path(audit_directory) if audit_directory is not None else None

    def _read(self):
        path = self.path / "session_state.json"
        try:
            if path.exists() and path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError
            state = self.manager.load_session_state(WORKSPACE_ID)
            if state is None:
                return {"schema_version": 1, "activeWorkspace": {"id": WORKSPACE_ID,
                        "displayName": "电商运营分析"}, "nodes": []}
            if not isinstance(state, dict) or state.get("schema_version") != 1 or not isinstance(state.get("nodes"), list):
                raise ValueError
            seen = set()
            if len(state["nodes"]) > 128:
                raise ValueError
            for node in state["nodes"]:
                if not isinstance(node, dict):
                    raise ValueError
                rid, question = node.get("request_id"), node.get("user_question")
                response = node.get("response")
                if (not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", rid)
                        or rid in seen or node.get("node_id") != rid
                        or not isinstance(question, str) or len(question.encode()) > 2048
                        or not isinstance(response, dict) or response.get("state") not in STATES
                        or node.get("chart") != CHART):
                    raise ValueError
                seen.add(rid)
            return state
        except (ValueError, TypeError, OSError):
            raise ToolError("WORKSPACE_UNAVAILABLE", "Saved workspace requires review") from None

    def _save(self, state):
        if len(json.dumps(state).encode()) > 20 * 1024 * 1024:
            raise ToolError("RESOURCE_LIMIT", "Workspace capacity reached")
        if not self.manager.workspace_exists(WORKSPACE_ID):
            self.manager.create_workspace(WORKSPACE_ID)
        self.manager.save_session_state(WORKSPACE_ID, state)

    def _task_lock(self, rid):
        return self.path / ("task-" + hashlib.sha256(rid.encode()).hexdigest() + ".lease")

    def read(self):
        with lease(self.path / "state.lease"):
            state = self._read()
            changed = False
            for node in state["nodes"]:
                if node["response"]["state"] != "running":
                    continue
                try:
                    with lease(self._task_lock(node["request_id"])):
                        recovered = None
                        if self.audit_directory is not None:
                            from data_formulator.ecommerce.analysis_service import completed_response
                            recovered = completed_response({"request_id": node["request_id"],
                                                            "user_question": node["user_question"]},
                                                           self.identity, self.audit_directory)
                        node["response"] = recovered if recovered is not None else {
                            **node["response"], "state": "interrupted", "error": {"code": "INTERRUPTED"}}
                        node["updated_at"] = datetime.now(timezone.utc).isoformat()
                        changed = True
                except ToolError as exc:
                    if exc.code != "BUSY":
                        raise
            if changed:
                self._save(state)
            return state

    def analyze(self, body, execute):
        if not isinstance(body, dict) or set(body) != {"request_id", "user_question"}:
            raise ToolError("INVALID_REQUEST", "Only request_id and user_question are accepted")
        rid, question = body["request_id"], body["user_question"]
        if (not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", rid)
                or not isinstance(question, str) or len(question.encode()) > 2048):
            raise ToolError("INVALID_REQUEST", "Invalid request ID or question")
        # Read also resolves abandoned leases before any possible new execution.
        state = self.read()
        with lease(self._task_lock(rid)):
            with lease(self.path / "state.lease"):
                state = self._read()
                old = next((n for n in state["nodes"] if n["request_id"] == rid), None)
                if old:
                    if old["user_question"] != question:
                        raise ToolError("REQUEST_CONFLICT", "Request ID belongs to another question")
                    return old["response"]
                if len(state["nodes"]) >= 128:
                    raise ToolError("RESOURCE_LIMIT", "Workspace task allowance exhausted")
                now = datetime.now(timezone.utc).isoformat()
                running = {"state": "running"}
                try:
                    running["conditions"] = bind_question(question, rid).payload()
                except ToolError as exc:
                    if exc.code != "CLARIFICATION_REQUIRED":
                        raise
                node = {"node_id": rid, "request_id": rid, "user_question": question,
                        "snapshot_id": SNAPSHOT_ID, "metric_version": METRIC_VERSION,
                        "created_at": now, "updated_at": now, "chart": CHART,
                        "response": running}
                state["nodes"].append(node)
                self._save(state)
            try:
                response = execute()
            except ToolError as exc:
                response = ({"state": "clarification_required", "question": exc.message, "executed": False}
                            if exc.code == "CLARIFICATION_REQUIRED" else error_result(exc.code, exc.message))
            except Exception:
                response = error_result("ANALYSIS_FAILED", "Restricted analysis failed")
            if "conditions" in running:
                response.setdefault("conditions", running["conditions"])
            with lease(self.path / "state.lease"):
                state = self._read()
                node = next(n for n in state["nodes"] if n["request_id"] == rid)
                node.update(response=response, updated_at=datetime.now(timezone.utc).isoformat())
                self._save(state)
            return response
