"""Bounded V0-9 real browser acceptance and independent raw CSV reference checks."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from devtools.reference_olist import reference
from devtools.run_local import ROOT


def stopped(port=5567):
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1', port)) != 0


def compare(current, baseline):
    with localcontext() as ctx:
        ctx.prec = 50
        def values(data):
            n, amount = Decimal(data['order_count']), Decimal(data['sales_amount'])
            return {'order_count': n, 'sales_amount': amount, 'average_order_amount': amount / n if n else None}
        def change(value, old, places):
            delta = value - old if value is not None and old is not None else None
            def fmt(number, places):
                return str(number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
            return {'absolute': fmt(delta, places) if delta is not None else None,
                    'percent': fmt(delta / old * 100, 4) if delta is not None and old else None}
        c, b = values(current), values(baseline)
        return {key: change(c[key], b[key], 0 if key == 'order_count' else 2) for key in c}


def verify_reference():
    raw = ROOT / 'data/raw/olist-v2'
    jan = reference(raw, '2018-01-01', '2018-02-01')
    feb = reference(raw, '2018-02-01', '2018-03-01')
    empty = reference(raw, '2016-11-01', '2016-12-01')
    groups = {r: reference(raw, '2018-01-01', '2018-02-01', r) for r in ('SP', 'RJ')}
    cases = json.loads((ROOT / 'docs/verification/V0-9-browser.json').read_text(encoding='utf-8'))['cases']
    result = cases[0]['response']['result']
    assert result['current']['values'] == feb and result['baseline']['values'] == jan
    assert result['changes'] == compare(feb, jan)
    result = cases[1]['response']['result']
    assert {g['key']: {k: v for k, v in g.items() if k != 'key'} for g in result['groups']} == groups
    assert result['values']['order_count'] == sum(v['order_count'] for v in groups.values())
    assert result['values']['sales_minor'] == sum(v['sales_minor'] for v in groups.values())
    assert result['values']['average_order_amount'] == str((Decimal(result['values']['sales_amount']) /
        result['values']['order_count']).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    result = cases[2]['response']['result']
    assert result['current']['values'] == jan and result['baseline']['values'] == empty
    assert result['changes'] == compare(jan, empty)
    assert cases[3]['response']['result']['values'] is None
    (ROOT / 'docs/verification/V0-9-reference.json').write_text(json.dumps({
        'method': 'fresh stdlib raw CSV + Decimal, independent of snapshot and metric implementation',
        'january': jan, 'february': feb, 'empty_baseline': empty, 'groups': groups,
        'feb_vs_jan': compare(feb, jan), 'zero_baseline': compare(jan, empty), 'all_equal': True,
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def main(restart_only=False):
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = json.loads(ledger.read_text(encoding='utf-8'))
    inherited = json.loads((ROOT / 'docs/verification/V0-8-usage.json').read_text(encoding='utf-8'))
    assert before[:len(inherited)] == inherited
    assert sum(max(r['reserved_cny'], r.get('estimated_cny', 0)) for r in before) + (0 if restart_only else .16) <= 10
    assert stopped(5173) and stopped(5567)
    success = False
    try:
        for phase in (('restart',) if restart_only else ('live', 'restart')):
            command = [sys.executable, '-m', 'devtools.run_ecommerce']
            if phase == 'live':
                command.append('--qwen')
            with (ROOT / f'.local/v09-{phase}.log').open('w', encoding='utf-8') as log:
                proc = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    deadline = time.monotonic() + 45
                    while stopped():
                        assert time.monotonic() < deadline and proc.poll() is None, 'Startup failed'
                        time.sleep(.3)
                    env = os.environ.copy()
                    env['NODE_PATH'] = str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules')
                    subprocess.run(['node', 'devtools/v09_browser.cjs', phase], cwd=ROOT, env=env,
                                   check=True, timeout=300)
                finally:
                    proc.terminate()
                    proc.wait(timeout=15)
                    deadline = time.monotonic() + 10
                    while not stopped():
                        assert time.monotonic() < deadline, 'Server did not stop'
                        time.sleep(.2)
        verify_reference()
        success = True
    finally:
        rows = json.loads(ledger.read_text(encoding='utf-8'))
        assert rows[:len(before)] == before and len(rows) - len(before) <= (0 if restart_only else 8)
        summary = {'status': 'passed' if success else 'failed', 'new_attempts': len(rows) - len(inherited),
                   'this_run_attempts': len(rows) - len(before),
                   'total_attempts': len(rows), 'attempts_with_usage': sum('input_tokens' in r for r in rows),
                   'input_tokens': sum(r.get('input_tokens', 0) for r in rows),
                   'output_tokens': sum(r.get('output_tokens', 0) for r in rows),
                   'estimated_cny': sum(r.get('estimated_cny', 0) for r in rows),
                   'reserved_cny': sum(r['reserved_cny'] for r in rows),
                   'prior_ledger_preserved': True, 'ports_stopped': stopped(5173) and stopped(5567)}
        (ROOT / 'docs/verification/V0-9-usage.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        (ROOT / 'docs/verification/V0-9-live-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart-only', action='store_true')
    main(parser.parse_args().restart_only)
