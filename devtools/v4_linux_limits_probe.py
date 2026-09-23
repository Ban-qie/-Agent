"""Standard-library-only Linux process behavior probe; no credentials or network."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    assert sys.platform == 'linux', 'Run inside the isolated Linux test container'
    source = Path(__file__).resolve().parents[1] / 'py-src/data_formulator/ecommerce/process_limits.py'
    spec = importlib.util.spec_from_file_location('limits', source)
    limits = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(limits)
    results = []
    proc = subprocess.Popen([sys.executable, '-I', '-c', 'import sys; sys.stdin.read()'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        try:
            limits.constrain_process(proc)
        except RuntimeError as error:
            assert 'session' in str(error)
        else:
            raise AssertionError('Worker without isolated session was accepted')
    finally:
        proc.kill()
        proc.communicate(timeout=2)
    results.append('missing_session_rejected')
    for name, code, expected in [
        ('normal', "import sys; sys.stdin.read(); print('ok')", b'ok'),
        ('memory', "import sys; sys.stdin.read(); a=bytearray(256*1024*1024)", None),
        ('timeout', "import sys,time; sys.stdin.read(); time.sleep(60)", None),
    ]:
        proc = subprocess.Popen([sys.executable, '-I', '-c', code], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        close = None
        timed_out = False
        try:
            close = limits.constrain_process(proc, 64 * 1024 * 1024)
            try:
                output, _ = proc.communicate(b'', timeout=1)
            except subprocess.TimeoutExpired:
                timed_out = True
            if name == 'normal':
                assert proc.returncode == 0 and output.strip() == expected
            elif name == 'memory':
                assert not timed_out and proc.returncode != 0
            else:
                assert timed_out
        finally:
            start = time.monotonic()
            if close:
                close()
            else:
                proc.kill()
            proc.communicate(timeout=2)
            assert time.monotonic() - start < 2
        results.append(name)
    # Parent waits on stdin while the descendant retains stdout. Cleanup must kill both.
    code = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); sys.stdin.read()"
    proc = subprocess.Popen([sys.executable, '-I', '-c', code], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    close = None
    try:
        close = limits.constrain_process(proc)
        import select
        assert select.select([proc.stdout], [], [], 2)[0], 'child PID deadline'
        child = int(proc.stdout.readline())
    finally:
        if close:
            close()
        else:
            proc.kill()
        proc.communicate(timeout=2)
    # Container init reaps orphans; a zombie is dead, never an executing worker.
    stat = Path(f'/proc/{child}/stat')
    assert not stat.exists() or stat.read_text().split(') ')[1].split()[0] == 'Z'
    results.append('descendant_cleanup')
    print(json.dumps({'passed': results, 'model_calls': 0}))


if __name__ == '__main__':
    main()
