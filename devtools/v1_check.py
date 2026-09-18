"""Offline real-browser V1 acceptance and actual server restart, preserving all ledgers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from devtools.run_local import ROOT
from devtools.v09_check import stopped


def main(stage, phase=None, qwen_baseline=False):
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = ledger.read_bytes()
    before_rows = json.loads(before)
    allowance = .08 if qwen_baseline else 0
    assert sum(max(row['reserved_cny'], row.get('estimated_cny', 0)) for row in before_rows) + allowance <= 10
    assert stopped(5567) and stopped(5173)
    try:
        for phase in ((phase,) if phase else ('v1', 'restart', 'v0')):
            env = os.environ.copy()
            env['ECOMMERCE_ANALYSIS_ORCHESTRATOR'] = 'v0' if phase == 'v0' else 'v1'
            env['NODE_PATH'] = str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules')
            env['V1_VERIFY_QWEN_BASELINE'] = 'true' if qwen_baseline else 'false'
            with (ROOT / f'.local/{stage}-{phase}.log').open('w', encoding='utf-8') as log:
                command = [sys.executable, '-m', 'devtools.run_ecommerce']
                if qwen_baseline and phase == 'v0':
                    command.append('--qwen')
                proc = subprocess.Popen(command, cwd=ROOT,
                                        env=env, stdout=log, stderr=log,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    deadline = time.monotonic() + 45
                    while stopped():
                        assert proc.poll() is None and time.monotonic() < deadline, 'Server startup failed'
                        time.sleep(.3)
                    subprocess.run(['node', 'devtools/v1_browser.cjs', stage, phase], cwd=ROOT,
                                   env=env, check=True, timeout=300)
                finally:
                    proc.terminate()
                    proc.wait(timeout=15)
                    deadline = time.monotonic() + 10
                    while not stopped():
                        assert time.monotonic() < deadline
                        time.sleep(.2)
    finally:
        after = ledger.read_bytes()
        rows = json.loads(after)
        assert rows[:len(before_rows)] == before_rows
        assert len(rows) - len(before_rows) <= (4 if allowance else 0)
        assert sum(max(row['reserved_cny'], row.get('estimated_cny', 0)) for row in rows) <= 10
        report = {'stage': stage, 'new_model_attempts': len(rows) - len(before_rows), 'ledger_unchanged': after == before,
                  'prior_ledger_preserved': True, 'total_attempts': len(rows),
                  'estimated_cny': sum(row.get('estimated_cny', 0) for row in rows),
                  'reserved_cny': sum(row['reserved_cny'] for row in rows),
                  'ledger_sha256': hashlib.sha256(after).hexdigest(), 'ports_stopped': stopped() and stopped(5173)}
        (ROOT / f'docs/verification/{stage}-usage.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['V1-14', 'V1-15'])
    parser.add_argument('--phase', choices=['v1', 'restart', 'v0'])
    parser.add_argument('--qwen-baseline', action='store_true', help='Explicitly allow at most four accounted model calls for V0 comparison')
    args = parser.parse_args()
    main(args.stage, args.phase, args.qwen_baseline)
