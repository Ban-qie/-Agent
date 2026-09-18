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


def main(stage):
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = ledger.read_bytes()
    assert stopped(5567) and stopped(5173)
    try:
        for phase in ('v1', 'restart', 'v0'):
            env = os.environ.copy()
            env['ECOMMERCE_ANALYSIS_ORCHESTRATOR'] = 'v0' if phase == 'v0' else 'v1'
            env['NODE_PATH'] = str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules')
            with (ROOT / f'.local/{stage}-{phase}.log').open('w', encoding='utf-8') as log:
                proc = subprocess.Popen([sys.executable, '-m', 'devtools.run_ecommerce'], cwd=ROOT,
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
        assert ledger.read_bytes() == before, 'Offline checks changed the ledger'
        report = {'stage': stage, 'new_model_attempts': 0, 'ledger_unchanged': True,
                  'ledger_sha256': hashlib.sha256(before).hexdigest(), 'ports_stopped': stopped() and stopped(5173)}
        (ROOT / f'docs/verification/{stage}-usage.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['V1-14', 'V1-15'])
    main(parser.parse_args().stage)
