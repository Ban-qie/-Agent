"""Persist reservations at the actual upstream Client dispatch boundary."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from data_formulator.agents.client_utils import Client
from data_formulator.ecommerce.contracts import ToolError, error_result

ROOT = Path(__file__).resolve().parents[3]
LEDGER = ROOT / ".local/verification/qwen-usage.json"
TOTAL_CNY = 10.0
RESERVE_CNY = 0.02
MAX_CALLS = 2
MAX_INPUT_BYTES = 16384  # Also a conservative input-token upper bound.
MAX_OUTPUT_TOKENS = 768
TASK_SECONDS = 60


@contextmanager
def exclusive(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.with_suffix(".lock").open("x")
    except FileExistsError:
        raise ToolError("BUSY", "Existing operation lock requires completion or interruption review") from None
    try:
        yield
    finally:
        handle.close()
        path.with_suffix(".lock").unlink()


def save_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class UsageLedger:
    def __init__(self, path=LEDGER):
        self.path = Path(path)

    def read(self):
        # Missing/corrupt history fails closed, never starts a new budget.
        try:
            if self.path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError
            rows = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(rows, list) or len(rows) < 5:
                raise ValueError
            for index, row in enumerate(rows, 1):
                if row["attempt"] != index:
                    raise ValueError
                for field in ("reserved_cny", "estimated_cny"):
                    value = row.get(field, 0)
                    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                        raise ValueError
                if row["reserved_cny"] <= 0:
                    raise ValueError
            return rows
        except (OSError, KeyError, TypeError, ValueError):
            raise ToolError("BUDGET_UNAVAILABLE", "Valid cumulative usage history is required") from None

    def reserve(self, task_id):
        with exclusive(self.path):
            rows = self.read()
            spent = sum(max(row["reserved_cny"], row.get("estimated_cny", 0)) for row in rows)
            if spent + RESERVE_CNY > TOTAL_CNY:
                raise ToolError("BUDGET_EXHAUSTED", "Cumulative project model budget exhausted")
            row = {"attempt": len(rows) + 1, "task_id": task_id, "stage": "V1-team" if task_id.startswith('v1-team:') else "V0-6",
                   "utc": datetime.now(timezone.utc).isoformat(), "state": "reserved",
                   "reserved_cny": RESERVE_CNY}
            rows.append(row)
            save_json(self.path, rows)
            return row["attempt"]

    def finish(self, attempt, updates):
        with exclusive(self.path):
            rows = self.read()
            rows[attempt - 1].update(updates)
            save_json(self.path, rows)


class BudgetClient(Client):
    """No ping, implicit retry or unaccounted completion; exactly one task/client."""
    max_calls = MAX_CALLS
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ledger = UsageLedger()
        self.task_id = "unbound"
        self.calls = 0
        self.deadline = time.monotonic() + TASK_SECONDS
        self.last_error = None

    def get_completion_with_tools(self, *args, **kwargs):
        try:
            return super().get_completion_with_tools(*args, **kwargs)
        except ToolError as exc:
            self.last_error = error_result(exc.code, exc.message)
            raise

    def ping(self, timeout=10):
        raise ToolError("TOOL_NOT_ALLOWED", "Implicit model calls are disabled")

    def _upstream_dispatch(self, **kwargs):
        from data_formulator.ecommerce.model_transport import dispatch
        return dispatch(self, **kwargs)

    def _dispatch(self, *, messages, stream, params, tools=None, extra=None):
        if self.task_id == "unbound" or self.calls >= self.max_calls:
            raise ToolError("CALL_LIMIT", "Task model-call allowance exhausted")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError("ANALYSIS_TIMEOUT", "Task deadline exceeded")
        payload = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
        if len(payload.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ToolError("TOKEN_LIMIT", "Model input byte/token allowance exceeded")
        options = {**params, **(extra or {})}
        options.pop("reasoning_effort", None)
        options.update(max_tokens=MAX_OUTPUT_TOKENS, timeout=min(10, remaining),
                       num_retries=0, max_retries=0, temperature=0, enable_thinking=False)
        if stream:
            options["stream_options"] = {"include_usage": True}
        self.calls += 1
        attempt = self.ledger.reserve(self.task_id)
        updates = {}

        def usage(response):
            value = getattr(response, "usage", None)
            if value is not None:
                inp, out = int(value.prompt_tokens), int(value.completion_tokens)
                if inp < 0 or out < 0:
                    raise ValueError
                updates.update(input_tokens=inp, output_tokens=out,
                               estimated_cny=(inp * .15 + out * 1.5) / 1e6)

        try:
            response = self._upstream_dispatch(messages=messages, stream=stream, params=options, tools=tools)
        except Exception as exc:
            self.ledger.finish(attempt, {"state": "error_or_unknown", "error_type": type(exc).__name__})
            if isinstance(exc, ToolError) and exc.code == 'ANALYSIS_TIMEOUT':
                raise
            raise ToolError("MODEL_FAILED", "Accounted model request failed") from None
        if not stream:
            try:
                usage(response)
                if time.monotonic() >= self.deadline:
                    raise ToolError('ANALYSIS_TIMEOUT', 'Task deadline exceeded')
            except Exception as exc:
                self.ledger.finish(attempt, {**updates, 'state': 'error_or_unknown'})
                if isinstance(exc, ToolError):
                    raise
                raise ToolError('MODEL_FAILED', 'Invalid model usage') from None
            self.ledger.finish(attempt, {**updates, "state": "response_received" if updates else 'usage_unknown'})
            return response

        def chunks():
            state = "error_or_unknown"
            output_bytes = 0
            try:
                for chunk in response:
                    if time.monotonic() > self.deadline:
                        raise TimeoutError
                    usage(chunk)
                    output_bytes += len(str(chunk).encode("utf-8"))
                    if output_bytes > 512 * 1024:
                        raise ValueError
                    yield chunk
                state = "response_received" if updates else "usage_unknown"
            except Exception as exc:
                updates["error_type"] = type(exc).__name__
                self.last_error = error_result("MODEL_FAILED", "Accounted model stream failed")
                raise ToolError("MODEL_FAILED", "Accounted model stream failed") from None
            finally:
                try:
                    if hasattr(response, "aclose"):
                        asyncio.run(response.aclose())
                finally:
                    self.ledger.finish(attempt, {**updates, "state": state})
        return chunks()


def configured_client(task_id, client_class=BudgetClient):
    import os
    if os.environ.get("QWEN_ENABLED", "false").lower() != "true":
        raise ToolError("MODEL_DISABLED", "Enable the server-side Qwen configuration explicitly")
    from data_formulator.model_registry import ModelRegistry
    config = ModelRegistry().get_config("global-qwen-qwen-flash")
    if not config or config.get("model") != "qwen-flash" or config.get("endpoint") != "openai" or config.get("api_base") != "https://dashscope.aliyuncs.com/compatible-mode/v1":
        raise ToolError("MODEL_DISABLED", "Approved server-side model configuration is unavailable")
    client = client_class.from_config(config)
    client.task_id = task_id
    return client
