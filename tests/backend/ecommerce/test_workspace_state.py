import json
import subprocess
import sys
import threading

import pytest

from data_formulator.datalake.workspace_manager import WorkspaceManager
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.workspace_state import AnalysisWorkspace, WORKSPACE_ID, lease

BODY = {"request_id": "workspace-test-001", "user_question": "分析2018年1月销售额"}


def workspace(path):
    return AnalysisWorkspace(WorkspaceManager(path), "local:tester")


@pytest.mark.parametrize("state", ["success", "empty_result", "outside_coverage", "failed", "clarification_required"])
def test_roundtrip_terminal_states_and_duplicate_without_execution(tmp_path, state):
    store = workspace(tmp_path)
    response = {"state": state, "conditions": {"snapshot_id": "snapshot", "metric_version": "v1"},
                "partial_result": {"result_id": "partial"}}
    assert store.analyze(BODY, lambda: response) == response
    restored = workspace(tmp_path)
    node = restored.read()["nodes"][0]
    assert node["response"] == response and node["user_question"] == BODY["user_question"]
    assert node["node_id"] == BODY["request_id"] and node["chart"]["kind"] == "sales_bar"
    assert restored.analyze(BODY, lambda: pytest.fail("must never execute again")) == response
    with pytest.raises(ToolError, match="another question"):
        restored.analyze({**BODY, "user_question": "different"}, lambda: None)


def test_running_can_be_read_and_never_reexecuted(tmp_path):
    store = workspace(tmp_path)
    entered, finish = threading.Event(), threading.Event()
    errors = []
    def execute():
        entered.set()
        assert finish.wait(10)
        return {"state": "success"}
    def run():
        try:
            store.analyze(BODY, execute)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(10)
        assert workspace(tmp_path).read()["nodes"][0]["response"]["state"] == "running"
        with pytest.raises(ToolError) as exc:
            store.analyze(BODY, lambda: pytest.fail("duplicate"))
        assert exc.value.code == "BUSY"
    finally:
        finish.set()
        thread.join(10)
    assert not errors
    assert store.read()["nodes"][0]["response"]["state"] == "success"


def test_real_process_exit_releases_lease_and_marks_interrupted(tmp_path):
    script = '''
import os, sys
from pathlib import Path
from data_formulator.datalake.workspace_manager import WorkspaceManager
from data_formulator.ecommerce.workspace_state import AnalysisWorkspace
s = AnalysisWorkspace(WorkspaceManager(Path(sys.argv[1])), "local:tester")
s.analyze({"request_id":"workspace-test-001","user_question":"question"}, lambda: os._exit(9))
'''
    assert subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=15).returncode == 9
    store = workspace(tmp_path)
    assert store.read()["nodes"][0]["response"]["state"] == "interrupted"
    assert store.analyze({**BODY, "user_question": "question"}, lambda: pytest.fail("restart"))["state"] == "interrupted"
    assert store.analyze({**BODY, "request_id": "explicit-new-001"}, lambda: {"state": "success"})["state"] == "success"


def test_corrupt_history_fails_closed_and_preserves_bytes(tmp_path):
    store = workspace(tmp_path)
    store.analyze(BODY, lambda: {"state": "success"})
    path = store.path / "session_state.json"
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        store.analyze(BODY, lambda: pytest.fail("unsafe restart"))
    assert exc.value.code == "WORKSPACE_UNAVAILABLE" and path.read_text() == "broken"


def test_clarification_saved_and_separate_identity_roots(tmp_path):
    store = workspace(tmp_path / "user-a")
    def clarify():
        raise ToolError("CLARIFICATION_REQUIRED", "请说明期间")
    assert store.analyze(BODY, clarify)["state"] == "clarification_required"
    assert workspace(tmp_path / "user-b").read()["nodes"] == []
    with pytest.raises(ToolError):
        store.analyze({**BODY, "result": {}}, lambda: None)


def test_atomic_session_failure_keeps_prior_state(tmp_path, monkeypatch):
    manager = WorkspaceManager(tmp_path)
    manager.create_workspace(WORKSPACE_ID)
    manager.save_session_state(WORKSPACE_ID, {"saved": 1})
    import data_formulator.datalake.workspace_manager as module
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        manager.save_session_state(WORKSPACE_ID, {"saved": 2})
    assert manager.load_session_state(WORKSPACE_ID) == {"saved": 1}
