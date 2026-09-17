"""Offline browser refresh + real server restart, using existing V0-7 cached results."""
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
    before = ledger.read_bytes()
    for phase in ('before-restart', 'after-restart'):
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1', 5567)) != 0
        with (ROOT / f'.local/v08-{phase}.log').open('w', encoding='utf-8') as log:
            proc = subprocess.Popen([sys.executable, '-m', 'devtools.run_ecommerce'], cwd=ROOT,
                                    stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 45
                while True:
                    with socket.socket() as sock:
                        if sock.connect_ex(('127.0.0.1', 5567)) == 0:
                            break
                    assert time.monotonic() < deadline and proc.poll() is None, 'Startup failed'
                    time.sleep(.3)
                env = os.environ.copy()
                env['NODE_PATH'] = str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules')
                subprocess.run(['node', 'devtools/v08_browser.cjs', phase], cwd=ROOT, env=env,
                               check=True, timeout=60)
            finally:
                proc.terminate()
                proc.wait(timeout=15)
                stopped = time.monotonic() + 10
                while True:
                    with socket.socket() as sock:
                        if sock.connect_ex(('127.0.0.1', 5567)) != 0:
                            break
                    assert time.monotonic() < stopped, 'Server port did not close'
                    time.sleep(.2)
                assert ledger.read_bytes() == before, 'Budget history changed'
    (ROOT / 'docs/verification/V0-8-usage.json').write_bytes(before)
    print(json.dumps({'browser_phases': 2, 'actual_server_restart': True, 'new_model_calls': 0,
                      'ledger_bytes_unchanged': True}))


if __name__ == '__main__':
    main()
