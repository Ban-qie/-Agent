from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_formulator.ecommerce.contracts import BoundMetricTool, ToolError, parse_request
from data_formulator.ecommerce.executor import MetricExecutor, run_worker, worker_environment
from data_formulator.ecommerce.metrics import METRIC_VERSION
from data_formulator.ecommerce.process_limits import constrain_process


@pytest.fixture
def payload():
    return {"request_id": "synthetic-request-001", "snapshot_id": "a" * 64,
            "metric_version": METRIC_VERSION, "operation": "summarize",
            "current": {"start": "2018-01-01", "end": "2018-02-01"}, "group_by": "region", "limit": 1}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "orders.parquet"
    pq.write_table(pa.table({"order_id": ["1", "2"], "purchase_at": [datetime(2018, 1, 3), datetime(2018, 1, 4)],
                            "status": ["delivered", "delivered"], "region": ["SP", "UNKNOWN"],
                            "item_count": [2, 1], "amount_minor": [123, 0]}), path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "quality": {"observed_purchase_min": "2018-01-01", "observed_purchase_max": "2018-03-31"}}


@pytest.fixture
def executor(tmp_path, source):
    return MetricExecutor(tmp_path / "audit.json", {"a" * 64: source})


@pytest.mark.parametrize("field,value", [("sql", "SELECT * FROM read_csv('secret')"), ("code", "print(1)"),
                                         ("path", "../../.env"), ("status", "canceled"),
                                         ("snapshot_id", "../outside"), ("limit", True), ("limit", 201),
                                         ("operation", "delete"), ("regions", ["SP'; DROP TABLE orders;--"])])
def test_rejects_code_sql_paths_and_invalid_options(payload, field, value):
    payload[field] = value
    with pytest.raises(ToolError, match="Invalid"):
        parse_request(payload)


def test_worker_result_readonly_truncation_and_cached_duplicate(payload, executor, source, monkeypatch):
    from data_formulator.ecommerce import executor as module
    before = Path(source["path"]).read_bytes()
    result = executor.execute("local:tester", parse_request(payload))
    assert result["state"] == "success" and result["values"]["sales_minor"] == 123
    assert result["values"]["order_count"] == 2 and result["values"]["average_order_amount"] == "0.62"
    assert result["truncated"] and result["total_groups"] == 2 and len(result["groups"]) == 1
    assert Path(source["path"]).read_bytes() == before
    monkeypatch.setattr(module, "run_worker", lambda *args: pytest.fail("Duplicate must not execute again"))
    assert executor.execute("local:tester", parse_request(payload)) == result
    payload["current"]["start"] = "2018-01-02"
    with pytest.raises(ToolError) as exc:
        executor.execute("local:tester", parse_request(payload))
    assert exc.value.code == "REQUEST_CONFLICT"


def test_source_tampering_blocks_and_error_is_idempotent(payload, executor, source):
    Path(source["path"]).write_bytes(b"tampered data")
    result = executor.execute("local:tester", parse_request(payload))
    assert result["error"]["code"] == "SOURCE_INTEGRITY"
    assert executor.execute("local:tester", parse_request(payload)) == result


def test_source_catalog_identity_and_bound_tool(payload, executor):
    request = parse_request(payload)
    with pytest.raises(ToolError) as exc:
        executor.execute("browser:spoof", request)
    assert exc.value.code == "ACCESS_DENIED"
    tool = BoundMetricTool(executor, "local:tester", request)
    assert tool.call("execute_python_script", payload)["error"]["code"] == "TOOL_NOT_ALLOWED"
    payload["current"] = {"start": "2018-02-01", "end": "2018-03-01"}
    assert tool.call("query_metrics", payload)["error"]["code"] == "CONDITION_MISMATCH"
    payload["snapshot_id"] = "b" * 64
    with pytest.raises(ToolError) as exc:
        executor.execute("local:tester", parse_request(payload))
    assert exc.value.code == "SOURCE_NOT_ALLOWED"


def test_worker_timeout_kills_entire_job(payload, source):
    started = time.monotonic()
    result = run_worker(parse_request(payload), source, timeout=0.001)
    assert result["error"]["code"] == "EXECUTION_TIMEOUT"
    assert time.monotonic() - started < 5


def test_empty_and_coverage_outside_are_not_execution_failures(payload, executor):
    payload["current"] = {"start": "2018-02-01", "end": "2018-03-01"}
    result = executor.execute("local:tester", parse_request(payload))
    assert result["state"] == "empty_result" and result["values"]["average_order_amount"] is None
    payload["request_id"] = "synthetic-request-002"
    payload["current"] = {"start": "2026-01-01", "end": "2026-02-01"}
    assert executor.execute("local:tester", parse_request(payload))["state"] == "outside_coverage"


def test_environment_excludes_all_inherited_secrets(monkeypatch):
    for key in ("qwen-api-key", "QWEN_API_KEY", "OPENAI_API_KEY", "UNUSUAL_PASSWORD", "PYTHONPATH"):
        monkeypatch.setenv(key, "fake-secret")
    env = worker_environment()
    assert "fake-secret" not in json.dumps(env)
    # Verify the actual child environment, not only the helper return value.
    output = subprocess.check_output([sys.executable, "-I", "-c", "import os,json;print(json.dumps(dict(os.environ)))"],
                                     env=env, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    assert b"fake-secret" not in output


def test_real_job_memory_cap():
    code = "import sys\nsys.stdin.readline()\ntry:\n x=bytearray(128*1024*1024)\n print('unexpected allocation')\nexcept MemoryError:\n print('memory limited')"
    proc = subprocess.Popen([sys.executable, "-I", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=worker_environment(), creationflags=subprocess.CREATE_NO_WINDOW)
    close = constrain_process(proc, memory_bytes=64 * 1024 * 1024)
    try:
        out, err = proc.communicate(b"go\n", timeout=10)
        assert b"memory limited" in out and b"unexpected allocation" not in out, (proc.returncode, err)
    finally:
        close()
        proc.communicate()


def test_busy_and_execution_count_limit(payload, executor, monkeypatch):
    from data_formulator.ecommerce import executor as module
    executor.audit_path.with_suffix(".lock").touch()
    with pytest.raises(ToolError) as exc:
        executor.execute("local:tester", parse_request(payload))
    assert exc.value.code == "BUSY"
    executor.audit_path.with_suffix(".lock").unlink()
    monkeypatch.setattr(module, "MAX_EXECUTIONS", 0)
    with pytest.raises(ToolError) as exc:
        executor.execute("local:tester", parse_request(payload))
    assert exc.value.code == "RESOURCE_LIMIT"
