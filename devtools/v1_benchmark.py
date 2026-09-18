"""Four shared HTTP tasks, interleaved 4-task blocks, no retries or cached samples."""
import argparse
from decimal import localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from devtools.v1_campaign import ROOT, campaign, read, create_once, verify_ledger, manifest, fix_campaign
from devtools.v1_campaign_reference import Reference
from devtools.v09_check import stopped

DIRECTORY = ROOT / 'docs/verification/V1-R06'
QUESTIONS = ['比较2018年2月与2018年1月销售额、订单数、客单价',
    '分析2018年1月销售额、订单数、客单价地区SP、RJ按地区分组',
    '分析2018年1月销售额、订单数、客单价按月分组',
    '分析2018年2月销售额、订单数、客单价按天分组']


def expected(index, mode):
    value = {'state': 'success', 'operation': 'compare' if index == 0 else 'summarize',
        'current': {'start': '2018-02-01', 'end': '2018-03-01'} if index in (0, 3) else {'start': '2018-01-01', 'end': '2018-02-01'},
        'baseline': {'start': '2018-01-01', 'end': '2018-02-01'} if index == 0 else None,
        'regions': ['RJ', 'SP'] if index == 1 else [], 'group_by': [None, 'region', 'month', 'day'][index]}
    if mode == 'v1':
        value.update(metrics=['order_count', 'sales_amount', 'average_order_amount'], sort=None, top_n=None,
                     order_status=['delivered'], time_field='purchase_at', sales_basis='item_price_excluding_freight')
    return value


def p95(values):
    return sorted(values)[math.ceil(.95 * len(values)) - 1] if values else None


def summarize(samples):
    result = {}
    for mode in ('v0', 'v1'):
        rows = [r for r in samples if r['mode'] == mode]
        success = [r for r in rows if r['response'].get('state') == 'success' and r['reference_equal']]
        result[mode] = {'attempted': len(rows), 'successful': len(success),
            'failure_rate': (len(rows) - len(success)) / len(rows) if rows else None,
            'p95_all_ms': p95([r['elapsed_ms'] for r in rows]),
            'p95_success_ms': p95([r['elapsed_ms'] for r in success]),
            'calls': sum(r['calls'] for r in rows), 'estimated_cny': sum(r['estimated_cny'] for r in rows),
            'reserved_cny': sum(r['reserved_cny'] for r in rows), 'unknown_usage_calls': sum(r['unknown_usage_calls'] for r in rows),
            'input_tokens': sum(r['input_tokens'] for r in rows), 'output_tokens': sum(r['output_tokens'] for r in rows)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--blocks', type=int, choices=range(1, 6), default=5,
                        help='Paired blocks, 4 tasks per mode each; smaller run is explicitly a small sample')
    parser.add_argument('--fix', action='store_true', help='20 current V1 samples; historical V0 comparison with timing bias')
    args = parser.parse_args()
    global DIRECTORY
    if args.fix:
        DIRECTORY = ROOT / 'docs/verification/V1-R06-fix/benchmark'
    value = fix_campaign() if args.fix else campaign()
    modes = ('v1',) if args.fix else ('v0', 'v1')
    prefix = 'v1-r06-fix-bench' if args.fix else 'v1-r06'
    ledger_path = ROOT / '.local/verification/qwen-usage.json'
    before = read(ledger_path)
    if not args.live:
        print({'dry_run': True, 'samples_per_mode': 4 * args.blocks, 'max_new_calls': (12 if args.fix else 20) * args.blocks,
               'absolute_call_cap': value['absolute_call_cap'], 'remaining_calls': value['absolute_call_cap'] - len(before)})
        return
    assert stopped() and stopped(5173)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    plan_path = DIRECTORY / 'plan.json'
    plan = {'blocks': args.blocks, 'questions': QUESTIONS, 'manifest': manifest(),
            'snapshot': '55d83079902eb6387b886abac02a38d22148ea24a4e9cc384dca0b0094f18d3f',
            'model': 'qwen-flash', 'temperature': 0, 'output_tokens': 768, 'deadline_seconds': 60,
            'order': 'V1 only; historical V0 timing bias disclosed' if args.fix else '4-task blocks alternating v0/v1; starting mode alternates per pair',
            'max_calls_per_task': {'v0': 2, 'v1': 3}}
    if plan_path.exists(): assert read(plan_path) == plan, 'Frozen benchmark plan differs'
    else: create_once(plan_path, plan)
    pending_calls = sum((2 if mode == 'v0' else 3) for block in range(args.blocks)
        for mode in modes for i in range(4) if not (DIRECTORY / f'{block}-{mode}-{i}.json').exists())
    verify_ledger(before, value['prefix_count'], value['prefix_hash'], needed=pending_calls, cap=value['absolute_call_cap'])
    oracle = Reference()
    samples = []
    for block in range(args.blocks):
        for mode in (modes if args.fix else (('v0', 'v1') if block % 2 == 0 else ('v1', 'v0'))):
            run_dir = DIRECTORY / f'block-{block}-{mode}'
            if all((DIRECTORY / f'{block}-{mode}-{i}.json').exists() for i in range(4)):
                samples.extend(read(DIRECTORY / f'{block}-{mode}-{i}.json') for i in range(4))
                continue
            run_dir.mkdir(exist_ok=True)
            env = os.environ.copy()
            env.update(CAMPAIGN_LIVE='1', CAMPAIGN_MODE=mode,
                       CAMPAIGN_R06_FIX='1' if args.fix else '0',
                       PYTHONPATH=str(ROOT / 'py-src'),
                       CAMPAIGN_RUNTIME=str(ROOT / ('.local/V1-R06-fix-benchmark' if args.fix else '.local/V1-R06') / mode), CAMPAIGN_INVOCATION=str(run_dir))
            with (run_dir / ('server-' + str(time.time_ns()) + '.log')).open('w', encoding='utf-8') as log:
                process = subprocess.Popen([sys.executable, '-m', 'devtools.v1_campaign_run', '--serve'],
                    cwd=ROOT, env=env, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    deadline = time.monotonic() + 45
                    while stopped():
                        assert process.poll() is None and time.monotonic() < deadline
                        time.sleep(.2)
                    for i, question in enumerate(QUESTIONS):
                        target = DIRECTORY / f'{block}-{mode}-{i}.json'
                        if target.exists():
                            samples.append(read(target)); continue
                        intent = DIRECTORY / f'{block}-{mode}-{i}.intent.json'
                        assert not intent.exists(), 'Prior attempt without result: review durable response; never auto-resubmit'
                        body = {'request_id': f'{prefix}-{block}-{mode}-{i}-001', 'user_question': question}
                        prior = read(ledger_path)
                        create_once(intent, {'body': body, 'ledger_before': len(prior), 'manifest': plan['manifest']})
                        start = time.perf_counter()
                        response, status = None, None
                        try:
                            request = Request('http://127.0.0.1:5567/api/ecommerce/analyze',
                                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
                            try:
                                stream = urlopen(request, timeout=90)
                            except HTTPError as exc:
                                stream = exc
                            with stream:
                                status, response = stream.status, json.load(stream)
                        except Exception as exc:
                            response = {'state': 'transport_unknown', 'error_type': type(exc).__name__}
                        elapsed = round((time.perf_counter() - start) * 1000, 3)
                        added = read(ledger_path)[len(prior):]
                        equal = False
                        if response.get('state') == 'success':
                            try:
                                with localcontext() as ctx:
                                    ctx.prec = 50
                                    oracle.verify(response, expected(i, mode))
                                equal = True
                            except AssertionError:
                                pass
                        record = {'block': block, 'mode': mode, 'task_index': i, 'body': body,
                            'http_status': status, 'response': response, 'reference_equal': equal, 'elapsed_ms': elapsed,
                            'calls': len(added), 'input_tokens': sum(r.get('input_tokens', 0) for r in added),
                            'output_tokens': sum(r.get('output_tokens', 0) for r in added),
                            'estimated_cny': sum(r.get('estimated_cny', 0) for r in added),
                            'reserved_cny': sum(r['reserved_cny'] for r in added),
                            'unknown_usage_calls': sum('input_tokens' not in r for r in added)}
                        create_once(target, record); samples.append(record)
                        print(json.dumps({'block': block, 'mode': mode, 'task': i, 'state': response.get('state'),
                                          'reference_equal': equal, 'elapsed_ms': elapsed}), flush=True)
                finally:
                    process.terminate(); process.wait(timeout=15)
                    deadline = time.monotonic() + 10
                    while not stopped():
                        assert time.monotonic() < deadline; time.sleep(.2)
    after = read(ledger_path)
    assert after[:len(before)] == before and manifest() == plan['manifest']
    report = {'summary': summarize(samples), 'p95_method': 'nearest rank: ceil(0.95*n), no excluded failures',
        'limitations': '20/mode or explicit small sample; sequential local HTTP, not concurrent load; latency excludes restart/reference computation; no speed/quality superiority claim',
        'total_calls': len(after), 'ledger_sha256': hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
        'ports_stopped': stopped() and stopped(5173)}
    create_once(DIRECTORY / ('summary-' + str(time.time_ns()) + '.json'), report)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
