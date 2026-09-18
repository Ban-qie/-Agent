"""Bounded current-V1 acceptance, isolated workspaces and the existing shared ledger.

Explicit --live runs real Edge/Qwen. No installation, automatic retries, or history deletion.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from devtools.run_local import ROOT, configure_offline
from devtools.v09_check import stopped


def serve():
    if os.environ['READINESS_LIVE'] == '1':
        from devtools.qwen_config import configure_qwen, read_user_key
        configure_qwen(read_user_key())
    else:
        configure_offline()
    os.environ['DATA_FORMULATOR_HOME'] = os.environ['READINESS_RUNTIME']
    # Verification-only outer cap, in addition to unchanged production task/cost limits.
    from data_formulator.ecommerce.budget import UsageLedger
    from data_formulator.ecommerce.contracts import ToolError
    reserve = UsageLedger.reserve
    def bounded(self, task_id):
        if len(self.read()) >= int(os.environ['READINESS_ATTEMPT_CAP']):
            raise ToolError('CALL_LIMIT', 'Readiness acceptance call cap reached')
        return reserve(self, task_id)
    UsageLedger.reserve = bounded
    from data_formulator.app import app
    app.run(host='127.0.0.1', port=5567, debug=False, use_reloader=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    if args.serve:
        return serve()
    if not args.live:
        parser.error('Use --live explicitly; at most 140 calls / CNY 2.80 reserved')
    assert stopped() and stopped(5173), 'Existing server must remain untouched'
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = json.loads(ledger.read_bytes())
    assert sum(max(r['reserved_cny'], r.get('estimated_cny', 0)) for r in before) + 2.8 <= 10
    run = 'V1-readiness-' + uuid.uuid4().hex[:10]
    report_dir = ROOT / 'docs/verification' / run
    report_dir.mkdir()
    source_files = subprocess.check_output(['git', 'ls-files', 'py-src/data_formulator/ecommerce',
        'py-src/data_formulator/routes/ecommerce.py', 'src/views/EcommerceWorkspace.tsx',
        'src/views/ecommerce.ts', 'uv.lock', 'yarn.lock'], cwd=ROOT).decode().splitlines()
    manifest = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files}
    (report_dir / 'manifest.json').write_text(json.dumps({'head': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(), 'files': manifest}, indent=2))
    passed, phases = False, []
    try:
        for phase in ('demo', 'restore', 'followup', 'bench-v0', 'bench-v1'):
            mode = 'v0' if phase == 'bench-v0' else 'v1'
            env = os.environ.copy()
            env.update(ECOMMERCE_ANALYSIS_ORCHESTRATOR=mode,
                READINESS_LIVE='0' if phase == 'restore' else '1',
                READINESS_RUNTIME=str(ROOT / '.local' / run / mode),
                READINESS_ATTEMPT_CAP=str(len(before) + 140),
                READINESS_EVIDENCE=str(report_dir),
                NODE_PATH=str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules'))
            with (report_dir / (phase + '-server.log')).open('w', encoding='utf-8') as log:
                proc = subprocess.Popen([sys.executable, '-m', 'devtools.v1_readiness', '--serve'],
                    cwd=ROOT, env=env, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    deadline = time.monotonic() + 45
                    while stopped():
                        assert proc.poll() is None and time.monotonic() < deadline, 'Server startup failed'
                        time.sleep(.3)
                    subprocess.run(['node', 'devtools/v1_readiness_browser.cjs', phase], cwd=ROOT,
                        env=env, check=True, timeout=1200)
                    phases.append(phase)
                    print('Completed ' + phase, flush=True)
                finally:
                    proc.terminate()
                    proc.wait(timeout=15)
                    deadline = time.monotonic() + 10
                    while not stopped():
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Readiness server port did not stop')
                        time.sleep(.2)
            assert stopped()
        passed = True
    finally:
        after = json.loads(ledger.read_bytes())
        assert after[:len(before)] == before
        assert len(after) - len(before) <= 140
        report = {'run': run, 'passed': passed, 'completed_phases': phases,
            'new_calls': len(after) - len(before), 'total_calls': len(after),
            'new_estimated_cny': sum(r.get('estimated_cny', 0) for r in after[len(before):]),
            'new_reserved_cny': sum(r['reserved_cny'] for r in after[len(before):]),
            'total_reserved_cny': sum(r['reserved_cny'] for r in after),
            'total_estimated_cny': sum(r.get('estimated_cny', 0) for r in after),
            'prior_ledger_preserved': True, 'ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest(),
            'ports_stopped': stopped() and stopped(5173)}
        (report_dir / 'summary.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
