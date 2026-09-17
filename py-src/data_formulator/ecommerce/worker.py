"""Fixed-function child process. No SQL, eval, exec, provider or network calls."""
import hashlib
import json
from pathlib import Path
import sys

from data_formulator.ecommerce.contracts import ToolError, parse_request
from data_formulator.ecommerce.metrics import METRIC_CONTRACT, compare_results, summarize_rows

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_ROWS = 200_000
MAX_OUTPUT_BYTES = 128 * 1024


def run(envelope):
    import pyarrow as pa
    import pyarrow.parquet as pq
    request = parse_request(envelope["request"])
    source = envelope["source"]  # Server catalog, never caller-supplied fields.
    path = Path(source["path"])
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ToolError("RESOURCE_LIMIT", "Snapshot byte limit exceeded")
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ToolError("SOURCE_INTEGRITY", "Snapshot fingerprint differs from accepted data")
    parquet = pq.ParquetFile(pa.BufferReader(raw))
    if parquet.metadata.num_rows > MAX_ROWS or sum(parquet.metadata.row_group(i).total_byte_size for i in range(parquet.num_row_groups)) > MAX_UNCOMPRESSED_BYTES:
        raise ToolError("RESOURCE_LIMIT", "Snapshot row or memory input limit exceeded")
    expected = {"order_id", "purchase_at", "status", "region", "item_count", "amount_minor"}
    if set(parquet.schema_arrow.names) != expected:
        raise ToolError("SOURCE_INTEGRITY", "Snapshot schema differs from accepted data")
    def rows():
        for batch in parquet.iter_batches(batch_size=4096, use_threads=False):
            yield from batch.to_pylist()
    def summary(period):
        result = summarize_rows(rows(), period, source["quality"], request.regions, request.group_by)
        all_groups = result["groups"]
        result["total_groups"] = len(all_groups)
        result["truncated"] = len(all_groups) > request.limit
        result["groups"] = all_groups[:request.limit]
        result["group_order"] = "key ascending; totals cover full selected snapshot"
        return result
    current = summary(request.current)
    result = compare_results(current, summary(request.baseline)) if request.baseline else current
    result.update({"request_id": request.request_id, "conditions": request.payload(),
                   "provenance": {"snapshot_id": request.snapshot_id, "parquet_sha256": source["sha256"],
                                  "metric_version": request.metric_version},
                   "contract": METRIC_CONTRACT,
                   "warnings": ["Snapshot observations only; complete business coverage is unconfirmed",
                                "Source currency and timezone are undeclared"], "retryable": False})
    result["result_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024:
            raise ToolError("RESOURCE_LIMIT", "Worker input exceeds limit")
        response = run(json.loads(raw))
        output = json.dumps(response)
        if len(output.encode()) > MAX_OUTPUT_BYTES:
            raise ToolError("RESOURCE_LIMIT", "Result exceeds byte limit")
    except ToolError as exc:
        output = json.dumps({"state": "failed", "error": {"code": exc.code, "message": exc.message}, "retryable": False})
    except Exception:
        output = json.dumps({"state": "failed", "error": {"code": "EXECUTION_FAILED", "message": "Snapshot execution failed"}, "retryable": False})
    print(output)
