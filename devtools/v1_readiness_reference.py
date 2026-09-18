"""Independently verify the captured three-case readiness run; no model calls."""
import csv
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
from pathlib import Path

from devtools.run_local import ROOT


def main():
    directory = ROOT / 'docs/verification/V1-readiness-6f8d69ec4d'
    raw = ROOT / 'data/raw/olist-v2'
    def read(name):
        with (raw / name).open(encoding='utf-8', newline='') as stream:
            return list(csv.DictReader(stream))
    customers = {r['customer_id']: r['customer_state'] or 'UNKNOWN'
                 for r in read('olist_customers_dataset.csv')}
    orders = {r['order_id']: {'day': r['order_purchase_timestamp'][:10],
        'region': customers[r['customer_id']], 'amount': Decimal(0), 'seen': False}
        for r in read('olist_orders_dataset.csv') if r['order_status'] == 'delivered'}
    for row in read('olist_order_items_dataset.csv'):
        if row['order_id'] in orders:
            orders[row['order_id']]['amount'] += Decimal(row['price'])
            orders[row['order_id']]['seen'] = True
    assert all(r['seen'] for r in orders.values())
    def fmt(value, places=2):
        return str(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
    def values(rows):
        n, amount = len(rows), sum((r['amount'] for r in rows), Decimal(0))
        return {'order_count': n, 'sales_minor': int(amount * 100), 'sales_amount': fmt(amount),
                'average_order_amount': fmt(amount / n) if n else None}
    checked = []
    cases = json.loads((directory / 'demo.json').read_text(encoding='utf-8'))['cases']
    for index, case in enumerate(cases):
        response = case['response']
        result, condition = response['result'], response['conditions']
        assert condition['current'] == {'start': '2018-02-01', 'end': '2018-03-01'}
        assert condition['baseline'] == {'start': '2018-01-01', 'end': '2018-02-01'}
        assert condition['regions'] == (['SP'] if index == 2 else [])
        assert condition['group_by'] == [None, 'region', 'day'][index]
        assert condition['metrics'] == (['order_count', 'sales_amount', 'average_order_amount'] if index == 0 else ['sales_amount'])
        totals, groups_by_period = [], []
        for label, start, end in [('current', '2018-02-01', '2018-03-01'), ('baseline', '2018-01-01', '2018-02-01')]:
            rows = [r for r in orders.values() if start <= r['day'] < end and (index != 2 or r['region'] == 'SP')]
            expected = values(rows)
            assert result[label]['values'] == expected
            grouping = {}
            if index:
                for row in rows:
                    key = row['region'] if index == 1 else row['day']
                    grouping.setdefault(key, []).append(row)
            groups = {key: values(group) for key, group in grouping.items()}
            assert {g['key']: {k: v for k, v in g.items() if k != 'key'} for g in result[label]['groups']} == groups
            totals.append(expected)
            groups_by_period.append(groups)
        def exact(total, metric):
            if metric == 'average_order_amount':
                return Decimal(total['sales_amount']) / total['order_count'] if total['order_count'] else None
            return Decimal(str(total[metric]))
        for metric in ['order_count', 'sales_amount', 'average_order_amount']:
            new, old = [exact(total, metric) for total in totals]
            delta = new - old if new is not None and old is not None else None
            assert result['changes'][metric] == {
                'absolute': fmt(delta, 0 if metric == 'order_count' else 2) if delta is not None else None,
                'percent': fmt(delta / old * 100, 4) if delta is not None and old else None}
        if index == 1:
            current, baseline = groups_by_period
            delta = {k: Decimal(current[k]['sales_amount']) - Decimal(baseline[k]['sales_amount']) for k in current}
            assert result['ranking'] == [{'key': k, 'absolute': fmt(delta[k])} for k in sorted(sorted(delta), key=delta.get)]
        checked.append({'case_index': index, 'request_id': case['body']['request_id'],
            'response_state': response['state'], 'totals_groups_changes_equal': True,
            'explicit_period_region_metric_conditions_equal': True,
            'inherited_sort_requires_review': index == 2,
            'null_rankings': sum(r['absolute'] is None for r in result.get('ranking', []))})
    report = {'method': 'fresh raw CSV / stdlib / Decimal; no production metric or normalizer calls',
              'cases': checked, 'numeric_equal': True,
              'source_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.glob('*.csv')},
              'limitation': 'Failed interpreter is NOT a successful end-to-end case; sort inheritance remains a separate semantic issue.'}
    (directory / 'reference.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'numeric_cases_checked': len(checked), 'numeric_equal': True, 'end_to_end_passed': False}))


if __name__ == '__main__':
    with localcontext() as context:
        context.prec = 50
        main()
