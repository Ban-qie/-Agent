"""Offline campaign definition and fail-closed resume rules; no provider imports."""
import hashlib
import json
from pathlib import Path
import re

from devtools.run_local import ROOT

DIRECTORY = ROOT / 'docs/verification/V1-R04'
SOURCE = ROOT / 'docs/verification/V1-readiness-6f8d69ec4d'
RUNTIME = ROOT / '.local/V1-readiness-6f8d69ec4d/v1'
ABSOLUTE_CALL_CAP = 193
FIX_CALL_CAP = 247
SOURCE_HASHES = {'demo.json': 'd293c8ca2dfa708cf84747f81556d4ab6ed4c7f6d67d00049343f1e9f1c44875',
    'manifest.json': '7f9aeafc7e298e2f02939fac90c324c2ab53e0c75f7b0243c3be9969a5fb7619'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def create_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2))


def confined(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Path outside expected private directory')
    return path


def verify_ledger(rows, prefix_count, prefix_hash, needed=0, cap=ABSOLUTE_CALL_CAP):
    if len(rows) < prefix_count or digest(rows[:prefix_count]) != prefix_hash:
        raise ValueError('Ledger history changed or shortened')
    if [r['attempt'] for r in rows] != list(range(1, len(rows) + 1)):
        raise ValueError('Nonsequential ledger')
    if len(rows) + needed > cap or sum(max(r['reserved_cny'], r.get('estimated_cny', 0)) for r in rows) + needed * .02 > 10:
        raise ValueError('Campaign or cumulative budget exhausted')


def manifest():
    paths = list((ROOT / 'py-src/data_formulator/ecommerce').glob('*.py')) + [
        ROOT / 'py-src/data_formulator/routes/ecommerce.py', ROOT / 'src/views/EcommerceWorkspace.tsx',
        ROOT / 'src/views/ecommerce.ts', ROOT / 'uv.lock', ROOT / 'yarn.lock']
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def fix_campaign():
    """A separate, immutable 60-call rerun allowance, never resetting old budgets."""
    target = ROOT / 'docs/verification/V1-R06-fix/benchmark-campaign.json'
    rows = read(ROOT / '.local/verification/qwen-usage.json')
    if not target.exists():
        assert len(rows) == 187, 'Review ledger before freezing rerun allowance'
        create_once(target, {'prefix_count': 187, 'prefix_hash': digest(rows),
            'absolute_call_cap': FIX_CALL_CAP, 'runtime': str(ROOT / '.local/V1-R06-fix-benchmark'),
            'reason': 'Two targeted fixes passed; rerun 20 V1 samples on current code. Preserve old V0 and disclose timing bias.'})
    value = read(target)
    assert value['absolute_call_cap'] == FIX_CALL_CAP and value['prefix_count'] == 187
    verify_ledger(rows, value['prefix_count'], value['prefix_hash'], cap=FIX_CALL_CAP)
    return value


def campaign():
    for name, expected in SOURCE_HASHES.items():
        if hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Historical evidence changed')
    ledger = read(ROOT / '.local/verification/qwen-usage.json')
    target = DIRECTORY / 'campaign.json'
    if not target.exists():
        old = read(SOURCE / 'demo.json')['cases']
        definitions = [('day', '地区SP按日显示销售额', 'success', 'regions'),
            ('branch', '按地区比较订单数', 'success', 'root'),
            ('month', '按月显示', 'success', 'root'),
            ('empty', '分析2016年11月销售额', 'empty_result', None),
            ('outside', '分析2020年1月销售额', 'empty_result', None),
            ('clarify', '最新两个完整月销售额', 'clarification_required', 'root'),
            ('clarified', '按地区分组', 'success', 'clarify'),
            ('quantity', '分析2018年1月销量', 'clarification_required', None)]
        cases = [{'name': name, 'body': old[i]['body'], 'expected': 'success', 'historical': True}
                 for i, name in enumerate(('root', 'regions'))]
        for name, question, expected, parent in definitions:
            body = {'request_id': 'v1-r04-' + name + '-001', 'user_question': question}
            if parent:
                body['parent_node_id'] = next(c['body']['request_id'] for c in cases if c['name'] == parent)
            cases.append({'name': name, 'body': body, 'expected': expected, 'historical': False})
        create_once(target, {'version': 1, 'runtime': str(RUNTIME), 'source_hashes': SOURCE_HASHES,
            'absolute_call_cap': ABSOLUTE_CALL_CAP, 'prefix_count': len(ledger), 'prefix_hash': digest(ledger),
            'cases': cases})
    value = read(target)
    if value['version'] != 1 or value['source_hashes'] != SOURCE_HASHES or value['absolute_call_cap'] != ABSOLUTE_CALL_CAP:
        raise ValueError('Campaign contract changed')
    if confined(value['runtime'], ROOT / '.local') != RUNTIME.resolve():
        raise ValueError('Runtime changed')
    verify_ledger(ledger, value['prefix_count'], value['prefix_hash'])
    ids, names = set(), set()
    for case in value['cases']:
        body = case['body']
        if not re.fullmatch('[A-Za-z0-9_-]{8,64}', body['request_id']) or case['name'] in names or body['request_id'] in ids:
            raise ValueError('Invalid/duplicate case ID')
        if body.get('parent_node_id') and body['parent_node_id'] not in ids:
            raise ValueError('Parent must be a prior case')
        ids.add(body['request_id']); names.add(case['name'])
    return value


def resume_action(body, node, rows, parent=None):
    if body.get('parent_node_id'):
        if not parent or parent['node_id'] != body['parent_node_id'] or parent['status'] not in {'success', 'waiting_clarification'}:
            raise ValueError('Parent missing, failed or active')
    if node:
        if node['question'] != body['user_question'] or node.get('parent_node_id') != body.get('parent_node_id'):
            raise ValueError('Request ID reused with different input')
        if node['status'] in {'running', 'interrupted', 'failed'}:
            raise ValueError('Existing failed/active/uncertain task requires explicit review')
        return 'recover'
    if any(r.get('task_id') == 'v1-team:' + body['request_id'] for r in rows):
        raise ValueError('Paid task without durable node; do not resubmit')
    return 'submit_same_id'
