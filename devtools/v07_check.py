"""Start existing backend/Vite for bounded browser checks, then stop both."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from devtools.run_local import ROOT


def main():
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = json.loads(ledger.read_text(encoding='utf-8'))
    assert sum(max(r['reserved_cny'], r.get('estimated_cny', 0)) for r in before) + .16 <= 10
    for port in (5173, 5567):
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1', port)) != 0
    procs, logs = [], []
    try:
        commands = [([sys.executable, '-m', 'devtools.run_ecommerce', '--qwen'], 'backend'),
                    (['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173', '--strictPort'], 'frontend')]
        for command, name in commands:
            log = (ROOT / f'.local/v07-{name}.log').open('w', encoding='utf-8')
            logs.append(log)
            procs.append(subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log,
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        deadline = time.monotonic() + 60
        for port in (5173, 5567):
            while True:
                with socket.socket() as sock:
                    if sock.connect_ex(('127.0.0.1', port)) == 0:
                        break
                assert time.monotonic() < deadline and all(p.poll() is None for p in procs), 'Startup failed'
                time.sleep(.3)
        env = os.environ.copy()
        env['NODE_PATH'] = str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules')
        subprocess.run(['node', 'devtools/v07_browser.cjs'], cwd=ROOT, env=env, check=True, timeout=300)
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait(timeout=15)
        for log in logs:
            log.close()
        after = json.loads(ledger.read_text(encoding='utf-8'))
        assert len(after) - len(before) <= 8
        (ROOT / 'docs/verification/V0-7-usage.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
        print(json.dumps({'new_attempts': len(after) - len(before), 'total_attempts': len(after),
                          'estimated_cny': sum(r.get('estimated_cny', 0) for r in after),
                          'reserved_cny': sum(r['reserved_cny'] for r in after)}))


if __name__ == '__main__':
    main()
