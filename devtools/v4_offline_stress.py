"""Offline 2 GiB capacity gate for the bounded multi-user task path.

The child process is placed in a Windows Job Object with a 2 GiB job limit.
The model client is never constructed: analysis is a deterministic stub that
holds a bounded working set while the HTTP/session/task boundaries are tested.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py-src"))

ORIGIN = "http://127.0.0.1:5567"
AUTH = "/api/ecommerce/auth"
CAP_BYTES = 2 * 1024 * 1024 * 1024


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_hashes():
    paths = [ROOT / "devtools/v4_offline_stress.py",
             ROOT / "py-src/data_formulator/ecommerce/task_service.py",
             ROOT / "py-src/data_formulator/ecommerce/process_limits.py",
             ROOT / "py-src/data_formulator/ecommerce/multiuser_app.py",
             ROOT / "deploy/ecommerce/compose.yml"]
    return {str(path.relative_to(ROOT)).replace("\\", "/"): file_hash(path) for path in paths}


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * fraction + 0.999999) - 1)))
    return round(ordered[index] * 1000, 3)


def current_rss_bytes():
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
                        ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
                        ("quota_peak_paged", ctypes.c_size_t), ("quota_paged", ctypes.c_size_t),
                        ("quota_peak_nonpaged", ctypes.c_size_t), ("quota_nonpaged", ctypes.c_size_t),
                        ("pagefile_usage", ctypes.c_size_t), ("peak_pagefile_usage", ctypes.c_size_t),
                        ("private_usage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.working_set)
        return 0
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value * (1024 if sys.platform != "darwin" else 1))
    except (ImportError, AttributeError):
        return 0


def wait_task(client, task_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/ecommerce/tasks/{task_id}", base_url=ORIGIN)
        if response.status_code != 200:
            raise AssertionError((response.status_code, response.get_json()))
        body = response.get_json()
        if body["status"] in {"success", "cancelled", "failed", "interrupted", "waiting_clarification", "empty_result"}:
            return body
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not finish")


def login(client, username, password):
    status = client.get(AUTH + "/status", base_url=ORIGIN)
    assert status.status_code == 200, status.get_json()
    csrf = status.get_json()["csrf_token"]
    response = client.post(AUTH + "/login", base_url=ORIGIN,
                           headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                           json={"username": username, "password": password})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["csrf_token"]


def child(output_path: Path, git_commit: str):
    from data_formulator.ecommerce.multiuser_app import create_app
    from data_formulator.ecommerce.multiuser_routes import install_routes
    from data_formulator.ecommerce.multiuser_service import MultiuserService
    from data_formulator.ecommerce.task_service import TaskService
    from data_formulator.ecommerce.task_store import TaskStore
    from data_formulator.ecommerce.website_usage import WebsiteUsage

    started_utc = time.time()
    root = output_path.parent / (output_path.stem + "-runtime")
    root.mkdir(parents=True, exist_ok=True)
    store = TaskStore(root / "multiuser.sqlite")
    store.initialize()
    users = [("alice", "offline-A"), ("bobby", "offline-B")] + [
        (f"user{index:02d}", f"offline-{index:02d}") for index in range(3, 11)]
    for username, password in users:
        store.create(username, password)

    started = threading.Event()
    release = threading.Event()
    active_lock = threading.Lock()
    active = 0
    peak_active = 0

    model_stub_calls = 0

    class OfflineClient:
        def __init__(self):
            self.answers = [
                {"action": "analyze", "canonical_question": "\u5206\u67902018\u5e741\u6708\u9500\u552e\u989d", "question": ""},
                {"decision": "approve", "question": ""},
                {"fact_ids": ["current.sales_amount", "scope"]},
            ]
            self.deadline = None
            self.checkpoint = None

        def get_completion(self, messages, **kwargs):
            del messages, kwargs
            nonlocal active, peak_active, model_stub_calls
            model_stub_calls += 1
            if len(self.answers) == 3:
                with active_lock:
                    active += 1
                    peak_active = max(peak_active, active)
                    started.set()
                try:
                    if not release.wait(10):
                        raise TimeoutError("offline stub release timeout")
                finally:
                    with active_lock:
                        active -= 1
            answer = self.answers.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))],
                usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))

    business = MultiuserService(store, root / "audit", lambda *_args: OfflineClient())
    service = TaskService(store, business, WebsiteUsage(store), max_workers=1, max_slots=1)
    app = create_app(root, secret_key=secrets.token_hex(32), origin=ORIGIN)
    install_routes(app, service)
    clients = [app.test_client() for _ in users]
    csrf = [login(client, *credentials) for client, credentials in zip(clients, users)]

    peak_rss = 0
    stop_monitor = threading.Event()

    def monitor():
        nonlocal peak_rss
        while not stop_monitor.is_set():
            peak_rss = max(peak_rss, current_rss_bytes())
            stop_monitor.wait(0.02)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    peak_rss = max(peak_rss, current_rss_bytes())
    read_latencies = []
    task_latencies = []
    rejected = None
    first_task = None
    cancelled_task = None
    first_completion_seconds = None
    try:
        body = {"request_id": "offline-stress-001", "user_question": "offline capacity probe"}
        started_at = time.perf_counter()
        first = clients[0].post("/api/ecommerce/analyze", base_url=ORIGIN,
                                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf[0]}, json=body)
        task_latencies.append(time.perf_counter() - started_at)
        assert first.status_code == 202, first.get_json()
        first_task = first.get_json()["task_id"]
        assert started.wait(5), "stub analysis did not start"

        duplicate = clients[0].post("/api/ecommerce/analyze", base_url=ORIGIN,
                                    headers={"Origin": ORIGIN, "X-CSRF-Token": csrf[0]}, json=body)
        assert duplicate.status_code == 202 and duplicate.get_json()["task_id"] == first_task

        started_at = time.perf_counter()
        second = clients[1].post(
            "/api/ecommerce/analyze", base_url=ORIGIN,
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf[1]},
            json={"request_id": "offline-stress-002", "user_question": "offline capacity probe"})
        rejected = {"status": second.status_code, "body": second.get_json(),
                    "latency_ms": round((time.perf_counter() - started_at) * 1000, 3)}
        assert second.status_code == 429 and second.get_json()["error"]["code"] == "BUSY"

        def read_round(client):
            start = time.perf_counter()
            response = client.get("/api/ecommerce/workspace", base_url=ORIGIN)
            return response.status_code, time.perf_counter() - start

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(read_round, client) for _round in range(4) for client in clients]
            for future in as_completed(futures):
                status, elapsed = future.result()
                assert status == 200
                read_latencies.append(elapsed)

        release.set()
        completion_started = time.perf_counter()
        completed = wait_task(clients[0], first_task)
        first_completion_seconds = time.perf_counter() - completion_started
        assert completed["status"] == "success", completed

        started.clear()
        release.clear()
        cancel_response = clients[0].post(
            "/api/ecommerce/analyze", base_url=ORIGIN,
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf[0]},
            json={"request_id": "offline-stress-003", "user_question": "offline cancellation"})
        assert cancel_response.status_code == 202, cancel_response.get_json()
        cancelled_task = cancel_response.get_json()["task_id"]
        assert started.wait(5)
        cancel = clients[0].post(f"/api/ecommerce/tasks/{cancelled_task}/cancel", base_url=ORIGIN,
                                 headers={"Origin": ORIGIN, "X-CSRF-Token": csrf[0]})
        assert cancel.status_code == 200 and cancel.get_json()["cancel_requested"]
        release.set()
        cancelled = wait_task(clients[0], cancelled_task)
        assert cancelled["status"] == "cancelled", cancelled
    finally:
        release.set()
        stop_monitor.set()
        monitor_thread.join(timeout=2)
        service.close()

    assert peak_rss > 0 and peak_rss < CAP_BYTES

    result = {
        "status": "passed",
        "mode": "offline_stub",
        "preflight_only": True,
        "resource_cap_bytes": CAP_BYTES,
        "resource_cap_enforced_by_parent_job": True,
        "dependency_backend": "SQLite TaskStore; PostgreSQL/Redis application migration is a later V4 step",
        "analysis_path": "real graph and Parquet worker with deterministic local model responses",
        "docker_dependency_stack_checked": False,
        "started_epoch": started_utc,
        "duration_seconds": round(time.time() - started_utc, 3),
        "git_commit": git_commit,
        "source_sha256": source_hashes(),
        "users": len(users),
        "analysis_slots": 1,
        "peak_active_analysis": peak_active,
        "peak_rss_bytes": peak_rss,
        "peak_rss_mib": round(peak_rss / (1024 * 1024), 3),
        "read_requests": len(read_latencies),
        "read_p50_ms": percentile(read_latencies, 0.50),
        "read_p95_ms": percentile(read_latencies, 0.95),
        "submit_p95_ms": percentile(task_latencies, 0.95),
        "first_task_completion_poll_seconds": round(first_completion_seconds, 3),
        "read_unexpected_5xx": 0,
        "task_unexpected_5xx": 0,
        "oom_or_restart_observed": False,
        "busy_rejection": rejected,
        "first_task_id": first_task,
        "cancelled_task_id": cancelled_task,
        "model_stub_calls": model_stub_calls,
        "qwen_calls": 0,
    }
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def next_output(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for path in directory.glob("offline-stress-attempt*.json"):
        try:
            attempts.append(int(path.stem.rsplit("attempt", 1)[1]))
        except (ValueError, IndexError):
            pass
    number = max(attempts, default=0) + 1
    return directory / f"offline-stress-attempt{number}.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()
    if args.child:
        child(args.child.resolve(), args.git_commit)
        return

    from data_formulator.ecommerce.process_limits import constrain_process

    output = next_output(ROOT / "docs/verification/V4-S04")
    ledger = ROOT / ".local/verification/qwen-usage.json"
    ledger_hash_before = file_hash(ledger) if ledger.is_file() else None
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True, check=False).stdout.strip()
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", str(output),
                                "--git-commit", commit])
    close_job = None
    error = None
    try:
        close_job = constrain_process(process, memory_bytes=CAP_BYTES, active_processes=8)
        return_code = process.wait(timeout=120)
        if return_code != 0:
            error = f"child exited with code {return_code}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
    finally:
        if close_job:
            close_job()

    if error:
        payload = {"status": "failed", "mode": "offline_stub", "resource_cap_bytes": CAP_BYTES,
                   "resource_cap_enforced_by_parent_job": close_job is not None, "error": error,
                   "qwen_calls": 0}
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        payload = json.loads(output.read_text(encoding="utf-8"))
        payload["resource_cap_enforced_by_parent_job"] = close_job is not None
        ledger_hash_after = file_hash(ledger) if ledger.is_file() else None
        payload["qwen_ledger_sha256_before"] = ledger_hash_before
        payload["qwen_ledger_sha256_after"] = ledger_hash_after
        if ledger_hash_before != ledger_hash_after:
            payload["status"] = "failed"
            payload["error"] = "Qwen ledger changed during offline test"
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(output), **payload}, indent=2))
    raise SystemExit(0 if payload.get("status") == "passed" else 1)


if __name__ == "__main__":
    main()
