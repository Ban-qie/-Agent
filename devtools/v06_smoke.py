"""Three bounded real-model HTTP cases; preserve all prior usage and task records."""
import contextlib
import argparse
import io
import json
import logging
from pathlib import Path
import socket
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from devtools.qwen_config import configure_qwen, read_user_key
from devtools.run_local import ROOT


def main(run_id="initial"):
    # Key is read only at explicit invocation; never included in reports/errors.
    key = read_user_key()
    configure_qwen(key)
    from data_formulator.ecommerce.budget import UsageLedger, TOTAL_CNY
    ledger = UsageLedger()
    before = ledger.read()
    if sum(max(r["reserved_cny"], r.get("estimated_cny", 0)) for r in before) + .12 > TOTAL_CNY:
        raise RuntimeError("Insufficient cumulative budget for three cases")
    for port in (5173, 5567):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("Expected local test ports to be stopped")
    from data_formulator.app import app
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 5567, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {"stage": "V0-6", "before_attempts": len(before), "cases": []}
    try:
        def post(path, payload):
            req = Request("http://127.0.0.1:5567" + path,
                          data=json.dumps(payload, ensure_ascii=False).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
            try:
                response = urlopen(req, timeout=90)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, json.loads(response.read())
        reference = json.loads((ROOT / "docs/verification/V0-4-reference.json").read_text(encoding="utf-8"))
        changes = json.loads((ROOT / "docs/verification/V0-4-metrics.json").read_text(encoding="utf-8"))["feb_vs_jan"]
        questions = ["比较2018年2月与2018年1月销售额、订单数、客单价",
                     "分析2018年1月销售额、订单数、客单价地区SP", "分析2016年11月销售额、订单数、客单价"]
        for index, question in enumerate(questions):
            body = {"request_id": f"v06-{run_id}-http-{index + 1:03}", "user_question": question}
            status, result = post("/api/ecommerce/analyze", body)
            report["cases"].append({"http_status": status, "response": result})
            assert status == 200 and result.get("agent_completed"), "Analysis did not complete its tool-feedback loop"
            data = result["result"]
            if index == 0:
                assert data["current"]["values"] == reference["cases"][1]["expected"]
                assert data["baseline"]["values"] == reference["cases"][0]["expected"]
                assert data["changes"] == changes
            else:
                assert data["values"] == reference["cases"][index + 1]["expected"]
                assert result["state"] == ("success" if index == 1 else "empty_result")
            count = len(ledger.read())
            duplicate_status, duplicate = post("/api/ecommerce/analyze", body)
            assert duplicate_status == status and duplicate == result
            assert len(ledger.read()) == count
        count = len(ledger.read())
        status, clarification = post("/api/ecommerce/analyze", {"request_id": "v06-clarify-001", "user_question": "最新两个完整月销售额"})
        assert status == 200 and clarification["state"] == "clarification_required"
        assert len(ledger.read()) == count
        status, blocked = post("/api/agent/analyst-streaming", {})
        assert status == 403
        report.update(status="passed", duplicate_no_new_calls=True, ambiguous_no_new_calls=True,
                      legacy_route_status=status)
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        rows = ledger.read()
        assert len(rows) - len(before) <= 6
        report.update(after_attempts=len(rows), new_attempts=len(rows) - len(before),
                      input_tokens=sum(r.get("input_tokens", 0) for r in rows),
                      output_tokens=sum(r.get("output_tokens", 0) for r in rows),
                      estimated_cny=sum(r.get("estimated_cny", 0) for r in rows),
                      reserved_cny=sum(r["reserved_cny"] for r in rows), server_stopped=not thread.is_alive())
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        assert key not in serialized
        (ROOT / "docs/verification/V0-6-http.json").write_text(serialized, encoding="utf-8")
        (ROOT / "docs/verification/V0-6-usage.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return {key: report[key] for key in ("status", "new_attempts", "after_attempts", "estimated_cny", "reserved_cny", "server_stopped")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="initial", choices=["initial", "network-retry"])
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            result = main(args.run_id)
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__}
    print(json.dumps(result))
    raise SystemExit(result["status"] != "passed")
