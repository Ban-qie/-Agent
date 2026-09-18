"""Explicit bounded live Qwen team acceptance; keeps all prior cost history."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from devtools.run_local import ROOT
from devtools.v09_check import stopped


def main(live, followup_only=False):
    assert stopped() and stopped(5173)
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = json.loads(ledger.read_text(encoding='utf-8'))
    assert sum(max(r['reserved_cny'], r.get('estimated_cny', 0)) for r in before) + (0.12 if live else 0) <= 10
    success = False
    try:
        for phase in (('live', 'restore') if live else ('restore',)):
            env = os.environ.copy()
            env['ECOMMERCE_ANALYSIS_ORCHESTRATOR'] = 'v1'
            env['NODE_PATH'] = str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules')
            env['V1_TEAM_FOLLOWUP_ONLY'] = 'true' if followup_only else 'false'
            command = [sys.executable, '-m', 'devtools.run_ecommerce'] + (['--qwen'] if phase == 'live' else [])
            with (ROOT / f'.local/v1-team-{phase}.log').open('w', encoding='utf-8') as log:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    deadline = time.monotonic() + 45
                    while stopped():
                        assert process.poll() is None and time.monotonic() < deadline
                        time.sleep(.3)
                    subprocess.run(['node', 'devtools/v1_team_browser.cjs', phase], cwd=ROOT, env=env,
                                   check=True, timeout=180)
                finally:
                    process.terminate()
                    process.wait(timeout=15)
                    deadline = time.monotonic() + 10
                    while not stopped():
                        assert time.monotonic() < deadline
                        time.sleep(.2)
        success = True
    finally:
        after = json.loads(ledger.read_text(encoding='utf-8'))
        assert after[:len(before)] == before and len(after) - len(before) <= (6 if live else 0)
        report = {'passed': success, 'new_attempts': len(after) - len(before), 'prior_ledger_preserved': True,
                  'total_attempts': len(after), 'estimated_cny': sum(r.get('estimated_cny', 0) for r in after),
                  'reserved_cny': sum(r['reserved_cny'] for r in after),
                  'new_estimated_cny': sum(r.get('estimated_cny', 0) for r in after[len(before):]),
                  'ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest(), 'ports_stopped': stopped() and stopped(5173)}
        (ROOT / f'docs/verification/V1-team-{ "live" if live else "restore" }-usage.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Allow at most 6 model calls, CNY 0.12 reserved in existing ledger')
    parser.add_argument('--followup-only', action='store_true', help='Reuse successful root evidence and only retry the child task')
    args = parser.parse_args()
    main(args.live, args.followup_only)
