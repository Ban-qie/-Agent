"""Fresh raw CSV/Decimal reference for V1 HTTP results, independent of metric code."""
import csv
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
from devtools.run_local import ROOT
from devtools.v09_check import compare


def main():
    raw = ROOT / 'data/raw/olist-v2'
    def read(name):
        with (raw / name).open(encoding='utf-8', newline='') as stream:
            return list(csv.DictReader(stream))
    customers = {r['customer_id']: r['customer_state'] or 'UNKNOWN' for r in read('olist_customers_dataset.csv')}
    orders = {r['order_id']: {'day': r['order_purchase_timestamp'][:10], 'region': customers[r['customer_id']],
                             'amount': Decimal(0), 'seen': False}
              for r in read('olist_orders_dataset.csv') if r['order_status'] == 'delivered'}
    for row in read('olist_order_items_dataset.csv'):
        if row['order_id'] in orders:
            orders[row['order_id']]['amount'] += Decimal(row['price'])
            orders[row['order_id']]['seen'] = True
    assert all(row['seen'] for row in orders.values())
    def values(rows):
        amount, count = sum((r['amount'] for r in rows), Decimal(0)), len(rows)
        return {'order_count': count, 'sales_amount': f'{amount:.2f}', 'sales_minor': int(amount * 100),
                'average_order_amount': str((amount / count).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if count else None}
    def reference(period, conditions):
        rows = [r for r in orders.values() if period['start'] <= r['day'] < period['end']
                and (not conditions['regions'] or r['region'] in conditions['regions'])]
        grouping = conditions['group_by']
        groups = {}
        if grouping:
            for row in rows:
                key = row['region'] if grouping == 'region' else row['day'] if grouping == 'day' else row['day'][:7]
                groups.setdefault(key, []).append(row)
        return values(rows), {key: values(group) for key, group in groups.items()}
    cases = json.loads((ROOT / 'docs/verification/V1-15-browser-v1.json').read_text(encoding='utf-8'))['cases']
    checked = []
    for case in cases:
        response = case['response']
        if response['state'] == 'clarification_required':
            assert response['executed'] is False and len(response['trace']) == 1
            continue
        result, conditions = response['result'], response['conditions']
        if result['state'] == 'outside_coverage':
            assert result.get('values') is None
            continue
        periods = [('current', result['current']), ('baseline', result['baseline'])] if 'current' in result else [('current', result)]
        refs = []
        for name, actual in periods:
            expected, groups = reference(conditions[name], conditions)
            assert actual['values'] == expected, (case['body']['user_question'], name)
            for group in actual['groups']:
                assert {key: value for key, value in group.items() if key != 'key'} == groups[group['key']]
            if not actual.get('truncated'):
                assert {g['key'] for g in actual['groups']} == set(groups)
            refs.append((expected, groups))
        if 'current' in result:
            assert result['changes'] == compare(refs[0][0], refs[1][0])
        sort = conditions.get('sort')
        if sort:
            field = sort['field']
            def numeric(group):
                if group is None or group[field] is None:
                    return None
                return Decimal(group['sales_amount']) / group['order_count'] if field == 'average_order_amount' else Decimal(str(group[field]))
            keys = sorted(set().union(*(set(g) for _, g in refs)))
            def rank_value(key):
                current = numeric(refs[0][1].get(key))
                if sort.get('basis') != 'change':
                    return current
                baseline = numeric(refs[1][1].get(key))
                return current - baseline if current is not None and baseline is not None else None
            ranked = sorted([key for key in keys if rank_value(key) is not None], key=rank_value,
                            reverse=sort['direction'] == 'desc') + [key for key in keys if rank_value(key) is None]
            ranked = ranked[:conditions['top_n']] if conditions.get('top_n') else ranked
            for (_, actual), (_, groups) in zip(periods, refs):
                assert [g['key'] for g in actual['groups']] == [key for key in ranked if key in groups]
            if result.get('ranking'):
                for row in result['ranking']:
                    expected = rank_value(row['key'])
                    assert (Decimal(row['absolute']) if row['absolute'] is not None else None) == expected
        checked.append({'request_id': case['body']['request_id'], 'question': case['body']['user_question'],
                        'v1_elapsed_ms': case['elapsed_ms'], 'numeric_and_conditions_equal': True,
                        'ranking': result.get('ranking')})
    v0 = json.loads((ROOT / 'docs/verification/V1-15-v0-comparison.json').read_text(encoding='utf-8'))
    comparisons = []
    for case in v0:
        peer = next(item for item in cases if item['body']['user_question'] == case['body']['user_question'])
        old, new = case['response']['result'], peer['response']['result']
        for key in ('current', 'baseline', 'values', 'groups', 'changes'):
            assert old.get(key) == new.get(key)
        for key in ('operation', 'current', 'baseline', 'regions', 'group_by'):
            assert case['response']['conditions'].get(key) == peer['response']['conditions'].get(key)
        comparisons.append({'question': case['body']['user_question'], 'equal': True,
                            'v0_elapsed_ms': case['elapsed_ms'], 'v1_elapsed_ms': peer['elapsed_ms'],
                            'v0_model_attempts': case['new_model_attempts'], 'v1_model_attempts': 0})
    report = {'method': 'fresh raw CSV and Decimal; no snapshot or metric implementation used for reference',
              'checked': checked, 'v0_v1_comparisons': comparisons, 'all_equal': True,
              'timing_limit': 'Single local samples; V0 invokes Qwen, V1 uses deterministic graph handlers. Not P95 or a model-quality comparison.'}
    (ROOT / 'docs/verification/V1-15-reference.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'checked': len(checked), 'v0_comparisons': len(comparisons), 'all_equal': True}))


if __name__ == '__main__':
    with localcontext() as context:
        context.prec = 50
        main()
