"""Bounded V0-2 probe through the upstream Client; no generated code execution.

Run from the project root: .venv/Scripts/python.exe -m devtools.qwen_smoke
Only this probe is budgeted. The web app remains offline until later stages.
"""
from __future__ import annotations

import contextlib
import argparse
import asyncio
from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
import time

from devtools.qwen_config import MODEL_ID, configure_qwen, read_user_key
from devtools.run_local import ROOT

LEDGER = ROOT / ".local" / "verification" / "qwen-usage.json"
MAX_ATTEMPTS = 6
MAX_INPUT_BYTES = 4096
MAX_OUTPUT = 256
# Official Beijing <=128K tier, checked 2026-09-17; no discount assumed.
INPUT_CNY_PER_M = 0.15
OUTPUT_CNY_PER_M = 1.5
# Conservative reservation per attempt, including unknown outcomes. The whole
# V0-2 probe is capped at 0.06 CNY out of the project's cumulative 10 CNY.
RESERVE_CNY = 0.01


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(self.rows, list):
            raise ValueError("Invalid usage ledger")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.rows, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def reserve(self):
        if len(self.rows) >= MAX_ATTEMPTS:
            raise RuntimeError("V0-2 attempt budget exhausted")
        row = {"attempt": len(self.rows) + 1, "utc": datetime.now(timezone.utc).isoformat(),
               "state": "reserved", "reserved_cny": RESERVE_CNY}
        self.rows.append(row)
        self.save()  # Reserve before network I/O, including interrupted requests.
        return row


def bounded_client(config, ledger):
    # Import only after configure_qwen and offline LiteLLM cost-map settings.
    from data_formulator.agents.client_utils import Client

    class ProbeClient(Client):
        def ping(self, timeout=10):
            raise RuntimeError("Use the accounted minimal completion probe")

        def _dispatch(self, *, messages, stream, params, tools=None, extra=None):
            payload = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
            if len(payload.encode("utf-8")) > MAX_INPUT_BYTES:
                raise ValueError("Probe input exceeds byte cap")
            options = {**params, **(extra or {})}
            # No SDK retries. Upstream compatibility retries re-enter this
            # method and therefore consume another persisted reservation.
            options.update(max_tokens=MAX_OUTPUT, timeout=15, num_retries=0,
                           max_retries=0, temperature=0, enable_thinking=False)
            options.pop("reasoning_effort", None)
            if stream:
                options["stream_options"] = {"include_usage": True}
            row = ledger.reserve()
            started = time.monotonic()

            def usage_of(response):
                usage = getattr(response, "usage", None)
                if usage is not None:
                    inp, out = int(usage.prompt_tokens), int(usage.completion_tokens)
                    row.update(input_tokens=inp, output_tokens=out,
                               estimated_cny=(inp * INPUT_CNY_PER_M + out * OUTPUT_CNY_PER_M) / 1e6)

            def finish(state):
                row.update(state=state, elapsed_seconds=round(time.monotonic() - started, 3))
                ledger.save()

            try:
                response = super()._dispatch(messages=messages, stream=stream, params=options, tools=tools)
            except Exception as exc:
                row["error_type"] = type(exc).__name__  # Never save str(exc).
                finish("error_or_unknown")
                raise RuntimeError("Model request failed; see sanitized usage ledger") from None
            if not stream:
                usage_of(response)
                finish("response_received")
                return response

            def chunks():
                try:
                    for chunk in response:
                        if time.monotonic() - started > 40:
                            raise TimeoutError("Probe stream deadline exceeded")
                        usage_of(chunk)
                        yield chunk
                    finish("response_received")
                except Exception as exc:
                    row["error_type"] = type(exc).__name__
                    finish("error_or_unknown")
                    raise RuntimeError("Model stream failed; see sanitized usage ledger") from None
                finally:
                    if hasattr(response, "aclose"):
                        asyncio.run(response.aclose())
                    else:
                        response.close()
            return chunks()

    return ProbeClient.from_config(config)


def probe(tools_only=False):
    ledger = Ledger(LEDGER)
    if len(ledger.rows) + (2 if tools_only else 4) > MAX_ATTEMPTS:
        raise RuntimeError("Insufficient remaining attempts for this probe")
    configure_qwen(read_user_key())
    from data_formulator.model_registry import ModelRegistry
    registry = ModelRegistry()
    public = registry.list_public()
    assert len(public) == 1 and public[0]["id"] == MODEL_ID
    config = registry.get_config(MODEL_ID)
    assert config["api_key"] not in json.dumps(public)
    client = bounded_client(config, ledger)

    if not tools_only:
        reply = client.get_completion([{"role": "user", "content": "Reply with exactly OK"}])
        assert reply.choices[0].message.content.strip() == "OK"
        reply = client.get_completion(
            [{"role": "user", "content": 'Return a JSON object exactly {"status":"ok","value":3}'}],
            response_format={"type": "json_object"})
        assert json.loads(reply.choices[0].message.content) == {"status": "ok", "value": 3}

    tools = [{"type": "function", "function": {
        "name": "add", "description": "Add two integers", "parameters": {
            "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"], "additionalProperties": False}}}]
    messages = [{"role": "user", "content": "Use add to add 2 and 3."}]
    calls = {}
    for chunk in client.get_completion_with_tools(messages, tools, stream=True,
            tool_choice={"type": "function", "function": {"name": "add"}}):
        for choice in chunk.choices:
            for tc in choice.delta.tool_calls or []:
                record = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    record["id"] = tc.id
                if tc.function:
                    record["name"] += tc.function.name or ""
                    record["arguments"] += tc.function.arguments or ""
    assert len(calls) == 1
    call = next(iter(calls.values()))
    assert call["id"] and call["name"] == "add"
    assert json.loads(call["arguments"]) == {"a": 2, "b": 3}
    # A fixed deterministic test tool only, never eval/exec or generated SQL.
    result = 2 + 3
    messages += [{"role": "assistant", "content": None, "tool_calls": [{
        "id": call["id"], "type": "function", "function": {
            "name": "add", "arguments": call["arguments"]}}]},
        {"role": "tool", "tool_call_id": call["id"], "content": str(result)},
        {"role": "user", "content": 'Return JSON only: {"result": <tool result>}'}]
    reply = client.get_completion(messages, response_format={"type": "json_object"})
    assert json.loads(reply.choices[0].message.content) == {"result": 5}
    return {"minimal": "skipped" if tools_only else "passed",
            "json_object": "skipped" if tools_only else "passed", "native_stream_tool": "passed",
            "tool_result_roundtrip": "passed", "model": public[0]["model"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-only", action="store_true", help="Rerun only tool probes; preserve prior usage")
    args = parser.parse_args()
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    # Single writer: a concurrent/repeated probe must not bypass reservations.
    lock = LEDGER.with_suffix(".lock")
    try:
        handle = lock.open("x")
    except FileExistsError:
        print('Probe already active or interrupted; inspect local ledger before retrying.')
        return 1
    try:
        # Third-party diagnostics must not print credentials or error bodies.
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                result = probe(tools_only=args.tools_only)
            except Exception as exc:
                result = {"status": "failed", "error_type": type(exc).__name__}
        name = "qwen-tools-smoke.json" if args.tools_only else "qwen-smoke.json"
        (LEDGER.parent / name).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return int(result.get("status") == "failed")
    finally:
        handle.close()
        lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
