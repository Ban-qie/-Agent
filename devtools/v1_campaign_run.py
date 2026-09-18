"""Resumable R04 acceptance. Default is an offline preflight, --live is explicit."""
import argparse
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

from devtools.v1_campaign import (ROOT, DIRECTORY, SOURCE, campaign, create_once, read,
    manifest, digest, verify_ledger, resume_action, fix_campaign)
from devtools.v09_check import stopped


def safe_capture(content, key):
    if not isinstance(content, str):
        return None
    value = content.replace(key, '[REDACTED]') if key else content
    value = re.sub(r'(?i)(bearer\s+)[\w.\-]+', r'\1[REDACTED]', value)
    value = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', value)
    return value[:8192]


def install_capture(value, directory, key):
    from data_formulator.ecommerce.budget import BudgetClient, UsageLedger
    from data_formulator.ecommerce.contracts import ToolError
    from devtools.v1_interpreter_diagnostic import classify
    # Original reserve acquires the ledger lock before invoking read(). Enforce
    # campaign limits there, not in a racy pre-read before original reserve.
    reserving = ContextVar('campaign_reserving', default=None)
    original_read, original_reserve = UsageLedger.read, UsageLedger.reserve
    def guarded_read(self):
        rows = original_read(self)
        task = reserving.get()
        try:
            verify_ledger(rows, value['prefix_count'], value['prefix_hash'], needed=1 if task else 0,
                          cap=value['absolute_call_cap'])
            if task and sum(r.get('task_id') == task for r in rows) >= (3 if task.startswith('v1-team:') else 2):
                raise ValueError('Task allowance already used across restarts')
        except ValueError:
            raise ToolError('CALL_LIMIT', 'Campaign budget/history requires review') from None
        return rows
    def reserve(self, task_id):
        token = reserving.set(task_id)
        try:
            return original_reserve(self, task_id)
        finally:
            reserving.reset(token)
    UsageLedger.read, UsageLedger.reserve = guarded_read, reserve
    dispatch = BudgetClient._dispatch
    def captured(self, *, messages, stream, params, tools=None, extra=None):
        if stream:
            return dispatch(self, messages=messages, stream=stream, params=params, tools=tools, extra=extra)
        prompt = str(messages[0].get('content', ''))
        role = next((r for r in ['Planner', 'Reviewer', 'Interpreter'] if 'Your role is ' + r in prompt or
                     (r == 'Reviewer' and 'independent semantic Reviewer' in prompt) or
                     (r == 'Interpreter' and 'evidence Interpreter' in prompt)), 'unknown')
        capture = {'task_id': self.task_id, 'role': role.lower()}
        try:
            result = dispatch(self, messages=messages, stream=stream, params=params, tools=tools, extra=extra)
            choice = result.choices[0]
            raw = choice.message.content
            capture.update(content=safe_capture(raw, key), finish_reason=getattr(choice, 'finish_reason', None))
            if role == 'Interpreter':
                facts = json.loads(messages[-1]['content']).get('facts', {})
                capture['classification'] = classify(raw, facts, capture['finish_reason'])
            return result
        except Exception as exc:
            capture['error_type'] = type(exc).__name__
            raise
        finally:
            wire = json.dumps(capture, ensure_ascii=False)
            assert not key or key not in wire
            create_once(directory / ('model-' + uuid.uuid4().hex + '.json'), capture)
    BudgetClient._dispatch = captured


def serve(value):
    from devtools.run_local import configure_offline
    key = ''
    if os.environ.get('CAMPAIGN_LIVE') == '1':
        from devtools.qwen_config import read_user_key, configure_qwen
        key = read_user_key()
        configure_qwen(key)
    else:
        configure_offline()
    os.environ['DATA_FORMULATOR_HOME'] = os.environ.get('CAMPAIGN_RUNTIME', value['runtime'])
    os.environ['ECOMMERCE_ANALYSIS_ORCHESTRATOR'] = os.environ.get('CAMPAIGN_MODE', 'v1')
    install_capture(value, Path(os.environ['CAMPAIGN_INVOCATION']), key)
    from data_formulator.app import app
    app.run(host='127.0.0.1', port=5567, debug=False, use_reloader=False)


def workspace():
    with urlopen('http://127.0.0.1:5567/api/ecommerce/workspace', timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--phase', choices=['demo', 'restore', 'followup', 'v0-restore'], default='demo')
    args = parser.parse_args()
    value = fix_campaign() if args.serve and os.environ.get('CAMPAIGN_R06_FIX') == '1' else campaign()
    if args.serve:
        return serve(value)
    if args.phase == 'followup':
        if not args.live:
            parser.error('Restored followup requires explicit --live')
        root = value['cases'][0]['body']['request_id']
        value['cases'] = [{'name': 'restored-followup', 'body': {'request_id': 'v1-r05-followup-001',
            'user_question': '按地区比较订单数', 'parent_node_id': root}, 'expected': 'success', 'historical': False}]
    ledger_path = ROOT / '.local/verification/qwen-usage.json'
    before = read(ledger_path)
    if not args.live and args.phase == 'demo':
        print(json.dumps({'dry_run': True, 'historical_parent_cases': 2,
            'pending_cases': [c['name'] for c in value['cases'] if not c['historical'] and not
                (DIRECTORY / 'cases' / (c['name'] + '.result.json')).exists()],
            'max_demo_new_calls': 18, 'absolute_call_cap': 193, 'remaining_calls': 193 - len(before)}))
        return
    assert stopped() and stopped(5173), 'Existing servers remain untouched'
    invocation = DIRECTORY / 'invocations' / uuid.uuid4().hex[:10]
    invocation.mkdir(parents=True)
    current_manifest = manifest()
    create_once(invocation / 'manifest.json', current_manifest)
    env = os.environ.copy()
    env.update(CAMPAIGN_LIVE='1' if args.live else '0', CAMPAIGN_INVOCATION=str(invocation),
        NODE_PATH=str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules'))
    if args.phase == 'v0-restore':
        assert not args.live
        env.update(CAMPAIGN_MODE='v0', CAMPAIGN_RUNTIME=str(ROOT / '.local/runtime'))
    passed = False
    try:
        with (invocation / 'server.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-m', 'devtools.v1_campaign_run', '--serve'],
                cwd=ROOT, env=env, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 45
                while stopped():
                    assert process.poll() is None and time.monotonic() < deadline, 'Server startup failed'
                    time.sleep(.2)
                if args.phase in ('demo', 'followup'):
                    for case in value['cases']:
                        nodes = workspace()['nodes']
                        body = case['body']
                        node = next((n for n in nodes if n['node_id'] == body['request_id']), None)
                        parent = next((n for n in nodes if n['node_id'] == body.get('parent_node_id')), None)
                        action = resume_action(body, node, read(ledger_path), parent)
                        case_dir = DIRECTORY / 'cases'
                        intent_path = case_dir / (case['name'] + '.intent.json')
                        if not intent_path.exists():
                            create_once(intent_path, {'case': case, 'manifest': current_manifest if not case['historical'] else read(SOURCE / 'manifest.json')['files'],
                                'ledger_before': len(read(ledger_path)), 'historical': case['historical']})
                        intent = read(intent_path)
                        assert intent['case'] == case, 'Intent changed'
                        if action != 'recover':
                            assert intent['manifest'] == current_manifest, 'Unsubmitted intent belongs to another code version'
                            verify_ledger(read(ledger_path), value['prefix_count'], value['prefix_hash'],
                                          needed=0 if case['expected'] == 'clarification_required' else 3)
                        elif case['historical']:
                            historical = next(c for c in read(SOURCE / 'demo.json')['cases'] if c['body'] == body)
                            assert node['result'] == historical['response'], 'Historical result changed'
                        subprocess.run(['node', 'devtools/v1_campaign_browser.cjs', case['name']],
                            cwd=ROOT, env=env, check=True, timeout=150)
                        print('Verified ' + case['name'], flush=True)
                if args.phase != 'followup':
                    subprocess.run(['node', 'devtools/v1_campaign_browser.cjs', args.phase + '-checks'],
                        cwd=ROOT, env=env, check=True, timeout=120)
                passed = True
            finally:
                process.terminate(); process.wait(timeout=15)
                deadline = time.monotonic() + 10
                while not stopped():
                    assert time.monotonic() < deadline, 'Server did not stop'
                    time.sleep(.2)
    finally:
        after = read(ledger_path)
        assert after[:len(before)] == before
        assert manifest() == current_manifest, 'Business code changed during invocation'
        report = {'phase': args.phase, 'passed': passed, 'new_calls': len(after) - len(before), 'total_calls': len(after),
            'estimated_cny': sum(r.get('estimated_cny', 0) for r in after[len(before):]),
            'reserved_cny': sum(r['reserved_cny'] for r in after[len(before):]),
            'ledger_sha256': hashlib.sha256(ledger_path.read_bytes()).hexdigest(), 'ports_stopped': stopped() and stopped(5173)}
        create_once(invocation / 'summary.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
