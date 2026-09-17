"""Read-only final integrity/secret/port checks; no model requests."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess

from devtools.qwen_config import read_user_key
from devtools.run_local import ROOT


def main(stage="V0-6", include_dist=False):
    key = read_user_key().encode()
    paths = subprocess.check_output(["git", "ls-files", "-m", "-o", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    files = {ROOT / path for path in paths if path and (ROOT / path).is_file()}
    for pattern in (".local/*.log", ".local/verification/*.json", ".local/runtime/ecommerce/*.json"):
        files.update(ROOT.glob(pattern))
    files.add(Path.home() / "Desktop/交互式电商运营数据分析 Agent开发文档.md")
    if include_dist:
        files.update((ROOT / 'py-src/data_formulator/dist').rglob('*.js'))
    hits = sum(key in path.read_bytes() for path in files)
    assert hits == 0, "Secret scan failed (paths withheld)"
    source = json.loads((ROOT / "docs/verification/V0-3-source.json").read_text(encoding="utf-8"))
    hashes = {}
    for entry in source["core_files"]:
        value = hashlib.sha256((ROOT / "data/raw/olist-v2" / entry["name"]).read_bytes()).hexdigest()
        assert value == entry["sha256"]
        hashes[entry["name"]] = value
    snapshot = json.loads((ROOT / "docs/verification/V0-3-snapshot.json").read_text(encoding="utf-8"))
    value = hashlib.sha256((ROOT / "data/processed/olist" / snapshot["snapshot_id"] / "orders.parquet").read_bytes()).hexdigest()
    assert value == snapshot["parquet_sha256"]
    for port in (5173, 5567):
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1", port)) != 0
    diff = subprocess.run(["git", "diff", "--check"], cwd=ROOT, capture_output=True)
    assert diff.returncode == 0
    locks = subprocess.check_output(["git", "diff", "--name-only", "--", "pyproject.toml", "uv.lock", "package.json", "yarn.lock"], cwd=ROOT)
    assert not locks.strip()
    report = {"secret_files_scanned": len(files), "exact_key_matches": hits,
              "raw_hashes_unchanged": hashes, "parquet_sha256": value,
              "ports_stopped": [5173, 5567], "git_diff_check": "passed", "dependency_lock_changes": []}
    (ROOT / f"docs/verification/{stage}-integrity.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
