"""Small durable store for V1 run nodes and parent-child branches.

V1 state is kept beside, but separately from, the V0 session schema so V0
recovery and its strict chart contract remain unchanged.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.workspace_state import lease, WORKSPACE_ID


MAX_NODES = 128
MAX_BYTES = 20 * 1024 * 1024
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
STATES = {"running", "waiting_clarification", "success", "empty_result", "partial", "failed", "interrupted"}


class V1WorkspaceStore:
    def __init__(self, manager, identity: str):
        if not isinstance(identity, str) or not identity.startswith("local:"):
            raise ToolError("ACCESS_DENIED", "Local identity required")
        self.manager = manager
        self.identity = identity
        self.path = manager.get_workspace_path(WORKSPACE_ID)
        self.file = self.path / "v1-session-state.json"
        self.lock = self.path / "v1-state.lease"

    def _default(self):
        return {"schema_version": 1, "orchestrator": "v1", "workspace_id": WORKSPACE_ID, "nodes": []}

    def _read_unlocked(self):
        try:
            if not self.file.exists():
                return self._default()
            if self.file.stat().st_size > MAX_BYTES:
                raise ValueError
            state = json.loads(self.file.read_text(encoding="utf-8"))
            if state.get("schema_version") != 1 or not isinstance(state.get("nodes"), list):
                raise ValueError
            seen = set()
            for node in state["nodes"]:
                if not isinstance(node, dict) or not NODE_ID.fullmatch(node.get("node_id", "")):
                    raise ValueError
                if node["node_id"] in seen or node.get("status") not in STATES:
                    raise ValueError
                parent = node.get("parent_node_id")
                if parent is not None and (not NODE_ID.fullmatch(parent) or parent == node["node_id"]):
                    raise ValueError
                if not isinstance(node.get("conditions"), dict) or not isinstance(node.get("question"), str):
                    raise ValueError
                seen.add(node["node_id"])
            if len(state["nodes"]) > MAX_NODES:
                raise ValueError
            node_ids = {node["node_id"] for node in state["nodes"]}
            if any(node.get("parent_node_id") not in node_ids for node in state["nodes"] if node.get("parent_node_id")):
                raise ValueError
            return state
        except (OSError, ValueError, TypeError, KeyError):
            raise ToolError("WORKSPACE_UNAVAILABLE", "V1 workspace requires review") from None

    def _save_unlocked(self, state):
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > MAX_BYTES:
            raise ToolError("RESOURCE_LIMIT", "V1 workspace capacity reached")
        self.path.mkdir(parents=True, exist_ok=True)
        temporary = self.file.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.file)

    def read(self):
        with lease(self.lock):
            state = self._read_unlocked()
            changed = False
            for node in state["nodes"]:
                if node["status"] == "running":
                    try:
                        with self.task_lease(node["node_id"]):
                            node["status"] = "interrupted"
                            node["error"] = {"code": "INTERRUPTED"}
                            node["result"] = {"state": "interrupted", "error": node["error"]}
                            node["updated_at"] = datetime.now(timezone.utc).isoformat()
                            changed = True
                    except ToolError as exc:
                        if exc.code != "BUSY":
                            raise
            if changed:
                self._save_unlocked(state)
            state["orchestrator"] = "v1"
            return state

    def task_lease(self, node_id):
        return lease(self.path / ("v1-task-" + hashlib.sha256(node_id.encode()).hexdigest() + ".lease"))

    def save_run(self, *, node_id: str, question: str, conditions: Mapping[str, Any],
                 status: str, result: Mapping[str, Any] | None = None,
                 chart_spec: Mapping[str, Any] | None = None,
                 parent_node_id: str | None = None, error: Mapping[str, Any] | None = None):
        if not NODE_ID.fullmatch(node_id) or status not in STATES:
            raise ToolError("INVALID_REQUEST", "Invalid V1 node or status")
        with lease(self.lock):
            state = self._read_unlocked()
            existing = next((item for item in state["nodes"] if item["node_id"] == node_id), None)
            if existing:
                if existing["question"] != question or (existing["status"] != "running" and existing["conditions"] != dict(conditions)):
                    raise ToolError("REQUEST_CONFLICT", "V1 node belongs to different conditions")
                existing["conditions"] = dict(conditions)
                existing.update(status=status, result=dict(result or {}), chart_spec=dict(chart_spec or {}),
                                error=dict(error or {}), updated_at=datetime.now(timezone.utc).isoformat())
                self._save_unlocked(state)
                return existing
            if parent_node_id and not any(item["node_id"] == parent_node_id for item in state["nodes"]):
                raise ToolError("PARENT_NOT_FOUND", "V1 parent node was not found")
            if len(state["nodes"]) >= MAX_NODES:
                raise ToolError("RESOURCE_LIMIT", "V1 workspace node allowance reached")
            now = datetime.now(timezone.utc).isoformat()
            node = {"node_id": node_id, "parent_node_id": parent_node_id, "question": question,
                    "conditions": dict(conditions), "status": status, "result": dict(result or {}),
                    "chart_spec": dict(chart_spec or {}), "error": dict(error or {}),
                    "created_at": now, "updated_at": now}
            state["nodes"].append(node)
            self._save_unlocked(state)
            return node

    def parent_conditions(self, node_id: str):
        state = self.read()
        node = next((item for item in state["nodes"] if item["node_id"] == node_id), None)
        if node is None:
            raise ToolError("PARENT_NOT_FOUND", "V1 parent node was not found")
        return dict(node["conditions"])

    def get_node(self, node_id: str):
        state = self.read()
        return next((dict(item) for item in state["nodes"] if item["node_id"] == node_id), None)
