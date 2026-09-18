"""Targeted live acceptance for the post-R06 fixes; preserves the original R06 evidence."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from decimal import localcontext
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from devtools.v1_campaign import ROOT, read, create_once, manifest
from devtools.v1_campaign_reference import Reference
from devtools.v1_benchmark import QUESTIONS, expected
from devtools.v09_check import stopped

DIRECTORY = ROOT / 'docs/verification/V1-R06-fix'
MAX_NEW_CALLS = 6


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    ledger_path = ROOT / '.local/verification/qwen-usage.json'
    before = read(ledger_path)
    if not args.live:
        print(json.dumps({'dry_run': True, 'total_calls': len(before),
                          'original_absolute_cap': 193, 'max_new_calls': MAX_NEW_CALLS}))
        return
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    frozen_manifest = manifest()
    plan = {'version': 1, 'kind': 'targeted-fix', 'cases': [
        {'name': 'strict-month', 'question': QUESTIONS[2], 'mode': 'v1', 'task_index': 2,
         'assertions': ['strict_conditions reviewer override', 'success']},
        {'name': 'day-interpreter', 'question': QUESTIONS[3], 'mode': 'v1', 'task_index': 3,
         'assertions': ['success', 'no invalid_id_count', 'at most six fact references']},
    ], 'max_new_calls': MAX_NEW_CALLS, 'parent_r06_total_calls': 181,
             'original_r06_evidence': 'docs/verification/V1-R06', 'manifest': frozen_manifest}
    plan_path = DIRECTORY / 'plan-v2.json'
    if plan_path.exists():
        assert read(plan_path) == plan
    else:
        create_once(plan_path, plan)
    pending = [c for c in plan['cases'] if not (DIRECTORY / (c['name'] + '.json')).exists()]
    for case in plan['cases']:
        existing = DIRECTORY / (case['name'] + '.json')
        if existing.exists():
            record = read(existing)
            assert record['manifest'] == frozen_manifest
            assert record['response'].get('state') == 'success', 'Previous failure requires review'
            with localcontext() as ctx:
                ctx.prec = 50
                Reference().verify(record['response'], expected(case['task_index'], 'v1'))
    assert len(before) + MAX_NEW_CALLS <= 193, 'R06-fix exceeds original absolute cap'
    assert stopped() and stopped(5173)
    invocation = DIRECTORY / 'invocations' / ('run-' + str(time.time_ns()))
    invocation.mkdir(parents=True)
    env = os.environ.copy()
    env.update(CAMPAIGN_LIVE='1', CAMPAIGN_MODE='v1',
               CAMPAIGN_RUNTIME=str(ROOT / '.local/V1-R06-fix'),
               CAMPAIGN_INVOCATION=str(invocation),
               PYTHONPATH=str(ROOT / 'py-src'),
               NODE_PATH=str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules'))
    with (invocation / 'server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-m', 'devtools.v1_campaign_run', '--serve'],
                                   cwd=ROOT, env=env, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic() + 45
            while stopped():
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(.2)
            for case in pending:
                target = DIRECTORY / (case['name'] + '.json')
                intent = DIRECTORY / (case['name'] + '.intent.json')
                body = {'request_id': 'v1-r06-fix-' + case['name'] + '-001',
                        'user_question': case['question']}
                assert not intent.exists(), 'Prior intent requires durable-result review; no automatic retry'
                if not intent.exists():
                    create_once(intent, {'body': body, 'ledger_before': len(read(ledger_path)),
                                         'manifest': frozen_manifest})
                start = time.perf_counter()
                try:
                    request = Request('http://127.0.0.1:5567/api/ecommerce/analyze',
                                      data=json.dumps(body, ensure_ascii=False).encode(),
                                      headers={'Content-Type': 'application/json'}, method='POST')
                    try:
                        stream = urlopen(request, timeout=90)
                    except HTTPError as exc:
                        stream = exc
                    with stream:
                        status, response = stream.status, json.load(stream)
                except Exception as exc:
                    status, response = None, {'state': 'transport_unknown', 'error_type': type(exc).__name__}
                elapsed = round((time.perf_counter() - start) * 1000, 3)
                added = read(ledger_path)[len(before):]
                record = {'case': case, 'body': body, 'http_status': status, 'response': response,
                          'elapsed_ms': elapsed, 'new_calls': len(added),
                          'ledger_before': len(before), 'ledger_after': len(read(ledger_path)),
                          'manifest': frozen_manifest}
                create_once(target, record)
                assert response.get('state') == 'success', record
                with localcontext() as ctx:
                    ctx.prec = 50
                    Reference().verify(response, expected(case['task_index'], 'v1'))
                collaboration = response.get('collaboration', {})
                if case['name'] == 'strict-month':
                    assert collaboration.get('review') == 'approve'
                    assert collaboration.get('review_override') == 'strict_conditions'
                    assert collaboration.get('review_model_decision') in {'clarify', 'approve'}
                else:
                    assert 1 <= len(response.get('explanation', {}).get('fact_ids', [])) <= 6
                    captures = list(invocation.glob('model-*.json'))
                    assert not any(c.exists() and 'invalid_id_count' in c.read_text(encoding='utf-8') for c in captures)
                print(json.dumps({'case': case['name'], 'status': status, 'state': response['state'],
                                  'new_calls': len(added), 'elapsed_ms': elapsed}, ensure_ascii=False), flush=True)
        finally:
            process.terminate(); process.wait(timeout=15)
            deadline = time.monotonic() + 10
            while not stopped():
                assert time.monotonic() < deadline
                time.sleep(.2)
    after = read(ledger_path)
    assert after[:len(before)] == before
    assert manifest() == frozen_manifest
    report = {'passed': True, 'cases': len(plan['cases']), 'new_calls': len(after) - len(before),
              'total_calls': len(after), 'max_new_calls': MAX_NEW_CALLS,
              'ledger_sha256': hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
              'original_r06_preserved': True, 'ports_stopped': stopped() and stopped(5173)}
    create_once(DIRECTORY / ('summary-' + str(time.time_ns()) + '.json'), report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
