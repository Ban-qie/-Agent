"""Replay only the saved interpreter input, once, without executing a tool.

Private evidence uses a fresh directory; original failed nodes and ledger are preserved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

from devtools.run_local import ROOT


def classify(content, facts, finish_reason=None):
    if finish_reason == 'length':
        return 'output_truncated'
    if not isinstance(content, str):
        return 'non_text_content'
    if len(content.encode('utf-8')) > 8192:
        return 'content_too_large'
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return 'invalid_json'
    if not isinstance(value, dict):
        return 'non_object_json'
    if set(value) != {'fact_ids'}:
        return 'invalid_fields'
    ids = value['fact_ids']
    if not isinstance(ids, list):
        return 'non_list_ids'
    if not 1 <= len(ids) <= 6:
        return 'invalid_id_count'
    if any(not isinstance(key, str) for key in ids):
        return 'non_string_id'
    if any(key not in facts for key in ids):
        return 'unknown_id'
    if len(set(ids)) != len(ids):
        return 'duplicate_id'
    return 'valid'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('One explicitly enabled live call required')
    from devtools.qwen_config import configure_qwen, read_user_key
    key = read_user_key()
    configure_qwen(key)
    from data_formulator.ecommerce.v1_agents import QwenTeam, verified_facts
    from data_formulator.ecommerce.budget import configured_client, LEDGER
    from data_formulator.ecommerce.contracts import ToolError
    source = ROOT / 'docs/verification/V1-readiness-6f8d69ec4d/demo.json'
    case = json.loads(source.read_text(encoding='utf-8'))['cases'][2]
    response = case['response']
    before = json.loads(LEDGER.read_bytes())
    assert sum(max(r['reserved_cny'], r.get('estimated_cny', 0)) for r in before) + .02 <= 10
    destination = ROOT / 'docs/verification/V1-R02' / uuid.uuid4().hex[:10]
    destination.mkdir(parents=True)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    captured = {}
    underlying = configured_client('v1-team:r02-' + destination.name)
    class CaptureClient:
        def get_completion(self, *a, **kw):
            assert not captured, 'No automatic diagnostic retry'
            captured['attempted'] = True
            result = underlying.get_completion(*a, **kw)
            choice = result.choices[0]
            content = choice.message.content
            # No request configuration, credential, raw rows or provider object is saved.
            captured.update(content=content.replace(key, '[REDACTED]') if isinstance(content, str) else None,
                            finish_reason=getattr(choice, 'finish_reason', None))
            return result
    state = {'user_question': case['body']['user_question'], 'normalized_conditions': response['conditions'],
             'verified_result': response['result'], 'plan': {'collaboration': response['collaboration']},
             'status': 'success'}
    facts = verified_facts(state['verified_result'])
    outcome = 'failed'
    try:
        result = QwenTeam('r02-diagnostic', CaptureClient()).interpreter(state)
        outcome = 'success'
        captured['selected_fact_ids'] = result['explanation']['fact_ids']
    except ToolError as exc:
        captured['error_code'] = exc.code
    finally:
        after = json.loads(LEDGER.read_bytes())
        assert after[:len(before)] == before and len(after) - len(before) <= 1
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
        report = {'source': str(source.relative_to(ROOT)), 'source_sha256': original_hash,
            'outcome': outcome, 'diagnosis': classify(captured.get('content'), facts, captured.get('finish_reason')),
            'allowed_fact_ids': list(facts), 'capture': captured, 'tool_executions': 0,
            'new_calls': len(after) - len(before), 'total_calls': len(after),
            'new_estimated_cny': sum(r.get('estimated_cny', 0) for r in after[len(before):]),
            'new_reserved_cny': sum(r['reserved_cny'] for r in after[len(before):]),
            'ledger_sha256': hashlib.sha256(LEDGER.read_bytes()).hexdigest(),
            'original_failure_cause_proven': False}
        wire = json.dumps(report, ensure_ascii=False, indent=2)
        assert key not in wire
        (destination / 'diagnostic.json').write_text(wire, encoding='utf-8')
        print(json.dumps({k: report[k] for k in ('outcome', 'diagnosis', 'new_calls', 'new_estimated_cny')}))
        print(str(destination.relative_to(ROOT)))


if __name__ == '__main__':
    main()
