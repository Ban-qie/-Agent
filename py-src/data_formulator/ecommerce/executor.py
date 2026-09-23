"""Read-only fixed-worker dispatch with identity-scoped idempotency and bounds."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from data_formulator.ecommerce.contracts import ToolError, error_result, parse_request
from data_formulator.ecommerce.process_limits import constrain_process

SNAPSHOT_ID = "55d83079902eb6387b886abac02a38d22148ea24a4e9cc384dca0b0094f18d3f"
PARQUET_SHA256 = "00dd35e2b5491739f634bcc861d0905646161a7d3f7cd84657700ec2653ac95a"
MAX_EXECUTIONS = 128
WORKER_TIMEOUT = 15


def accepted_catalog(root=None):
    root = (Path(__file__).resolve().parents[3] / "data/processed/olist"
            if root is None else Path(root))
    root = root.resolve()
    path = root / SNAPSHOT_ID / "orders.parquet"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ToolError("SOURCE_INTEGRITY", "Snapshot path escapes the configured data directory")
    return {SNAPSHOT_ID: {"path": str(path), "sha256": PARQUET_SHA256,
                         "quality": {"observed_purchase_min": "2016-09-04 21:15:19",
                                     "observed_purchase_max": "2018-10-17 17:30:18"}}}


def worker_environment():
    # Allowlist, not suffix-based stripping: no provider keys, dotenv or user profile.
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    env.update({"PYTHON_DOTENV_DISABLED": "1", "PYTHONIOENCODING": "utf-8",
                "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
    return env


def run_worker(request, source, timeout=WORKER_TIMEOUT, checkpoint=None):
    process = subprocess.Popen([sys.executable, "-I", "-m", "data_formulator.ecommerce.worker"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=worker_environment(), start_new_session=sys.platform == 'linux',
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    close_job = None
    try:
        close_job = constrain_process(process)
        wire = json.dumps({"request": request.payload(), "source": source}).encode()
        if checkpoint is None:
            output, _ = process.communicate(wire, timeout=timeout)
        else:
            from data_formulator.ecommerce.process_wait import communicate
            output, _ = communicate(process, wire, time.monotonic() + timeout, checkpoint)
        if process.returncode != 0:
            return error_result("EXECUTION_FAILED", "Worker failed or hit a resource limit")
        if len(output) > 128 * 1024:
            return error_result("RESOURCE_LIMIT", "Result byte limit exceeded")
        return json.loads(output)
    except subprocess.TimeoutExpired:
        return error_result("EXECUTION_TIMEOUT", "Worker deadline exceeded; conditions were not changed")
    except Exception:
        return error_result("EXECUTION_FAILED", "Restricted worker could not complete")
    finally:
        if process.poll() is None:
            process.kill()
        if close_job:
            close_job()
        process.communicate(timeout=2)


class MetricExecutor:
    def __init__(self, audit_path: Path, catalog=None, *, context=None):
        self.audit_path = Path(audit_path)
        self.catalog = accepted_catalog() if catalog is None else catalog
        self.context = context
        if context is not None:
            from data_formulator.ecommerce.execution_context import ExecutionContext
            if not isinstance(context, ExecutionContext):
                raise ToolError('ACCESS_DENIED', 'Verified execution context required')
            scope = hashlib.sha256((context.principal.owner + '\0' + context.workspace).encode()).hexdigest()
            self.audit_path = self.audit_path.parent / scope / self.audit_path.name

    def execute(self, identity: str, request):
        request = parse_request(request.payload())
        if self.context is not None:
            identity = self.context.check(identity, request)
        elif not isinstance(identity, str) or not identity.startswith("local:"):
            raise ToolError("ACCESS_DENIED", "Only authenticated local mode is supported in V0")
        if request.snapshot_id not in self.catalog:
            raise ToolError("SOURCE_NOT_ALLOWED", "Snapshot is not in the accepted server catalog")
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.audit_path.with_suffix(".lock")
        try:
            lock = lock_path.open("x")
        except FileExistsError:
            raise ToolError("BUSY", "Another execution is active or requires interruption review") from None
        try:
            if self.audit_path.exists() and self.audit_path.stat().st_size > 20 * 1024 * 1024:
                raise ToolError("RESOURCE_LIMIT", "Execution audit size limit reached")
            records = json.loads(self.audit_path.read_text(encoding="utf-8")) if self.audit_path.exists() else {}
            key = hashlib.sha256((identity + "\0" + request.request_id).encode()).hexdigest()
            fingerprint = request.fingerprint()
            previous = records.get(key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ToolError("REQUEST_CONFLICT", "Request ID already belongs to different conditions")
                if previous["status"] != "completed":
                    raise ToolError("INTERRUPTED", "Previous execution was interrupted; review before issuing a new request")
                return previous["result"]
            if len(records) >= MAX_EXECUTIONS:
                raise ToolError("RESOURCE_LIMIT", "V0 execution allowance reached; review before increasing it")
            def save():
                temp = self.audit_path.with_suffix(".tmp")
                temp.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
                temp.replace(self.audit_path)
            records[key] = {"fingerprint": fingerprint, "status": "running", "started_epoch": time.time()}
            save()
            if self.context is not None and self.context.checkpoint is not None:
                result = run_worker(request, self.catalog[request.snapshot_id], checkpoint=self.context.checkpoint)
            else:
                result = run_worker(request, self.catalog[request.snapshot_id])
            records[key].update(status="completed", result=result)
            save()
            return result
        finally:
            lock.close()
            lock_path.unlink()
