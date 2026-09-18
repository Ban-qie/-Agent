"""Raw CSV + Decimal oracle. Explicit expected conditions, no production calculator/parser."""
import argparse
import csv
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
import uuid

from devtools.v1_campaign import ROOT, DIRECTORY, read, create_once


def fmt(value, places=2):
    return str(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def values(rows):
    n, amount = len(rows), sum((r['amount'] for r in rows), Decimal(0))
    return {'order_count': n, 'sales_minor': int(amount * 100), 'sales_amount': fmt(amount),
            'average_order_amount': fmt(amount / n) if n else None}


class Reference:
    def __init__(self):
        raw = ROOT / 'data/raw/olist-v2'
        def rows(name):
            with (raw / name).open(encoding='utf-8', newline='') as stream:
                return list(csv.DictReader(stream))
        customers = {r['customer_id']: r['customer_state'] or 'UNKNOWN' for r in rows('olist_customers_dataset.csv')}
        self.orders = {r['order_id']: {'day': r['order_purchase_timestamp'][:10],
            'region': customers[r['customer_id']], 'amount': Decimal(0), 'seen': False}
            for r in rows('olist_orders_dataset.csv') if r['order_status'] == 'delivered'}
        for row in rows('olist_order_items_dataset.csv'):
            if row['order_id'] in self.orders:
                self.orders[row['order_id']]['amount'] += Decimal(row['price'])
                self.orders[row['order_id']]['seen'] = True
        assert all(r['seen'] for r in self.orders.values())

    def period(self, bounds, regions, grouping):
        rows = [r for r in self.orders.values() if bounds['start'] <= r['day'] < bounds['end']
                and (not regions or r['region'] in regions)]
        groups = {}
        if grouping:
            for row in rows:
                key = row['region'] if grouping == 'region' else row['day'] if grouping == 'day' else row['day'][:7]
                groups.setdefault(key, []).append(row)
        return values(rows), {key: values(group) for key, group in groups.items()}

    def verify(self, response, expected):
        assert response['state'] == expected['state']
        if expected['state'] == 'clarification_required':
            assert not response['executed'] and response['budget']['model_calls'] == 0
            return
        condition, result = response['conditions'], response['result']
        for key, value in expected.items():
            if key != 'state': assert condition.get(key) == value, key
        if expected['current']['start'] == '2020-01-01':
            assert result['state'] == 'outside_coverage' and result['values'] is None
            return
        refs = []
        for label, actual in ([('current', result['current']), ('baseline', result['baseline'])]
                               if expected.get('baseline') else [('current', result)]):
            total, groups = self.period(expected[label], expected['regions'], expected['group_by'])
            assert actual['values'] == total
            assert {g['key']: {k: v for k, v in g.items() if k != 'key'} for g in actual['groups']} == groups
            refs.append((total, groups))
        if expected.get('baseline'):
            def exact(v, metric):
                return Decimal(v['sales_amount']) / v['order_count'] if metric == 'average_order_amount' and v['order_count'] else (
                    None if metric == 'average_order_amount' else Decimal(str(v[metric])))
            for metric in ['order_count', 'sales_amount', 'average_order_amount']:
                new, old = [exact(total, metric) for total, _ in refs]
                delta = new - old if new is not None and old is not None else None
                assert result['changes'][metric] == {'absolute': fmt(delta, 0 if metric == 'order_count' else 2) if delta is not None else None,
                    'percent': fmt(delta / old * 100, 4) if delta is not None and old else None}
        if expected.get('sort'):
            cur, base = [groups for _, groups in refs]
            delta = {key: Decimal(cur[key]['sales_amount']) - Decimal(base[key]['sales_amount']) for key in cur}
            ranked = sorted(sorted(delta), key=delta.get)
            assert result['ranking'] == [{'key': key, 'absolute': fmt(delta[key])} for key in ranked]
            assert [g['key'] for g in result['current']['groups']] == ranked
        else:
            assert not result.get('ranking')


def expectations():
    base = {'state': 'success', 'operation': 'compare', 'current': {'start': '2018-02-01', 'end': '2018-03-01'},
        'baseline': {'start': '2018-01-01', 'end': '2018-02-01'}, 'regions': [], 'group_by': None,
        'metrics': ['order_count', 'sales_amount', 'average_order_amount'], 'sort': None, 'top_n': None,
        'order_status': ['delivered'], 'time_field': 'purchase_at', 'sales_basis': 'item_price_excluding_freight'}
    return {'root': base, 'regions': {**base, 'group_by': 'region', 'metrics': ['sales_amount'],
            'sort': {'field': 'sales_amount', 'direction': 'asc', 'basis': 'change'}},
        'day': {**base, 'group_by': 'day', 'regions': ['SP'], 'metrics': ['sales_amount']},
        'branch': {**base, 'group_by': 'region', 'metrics': ['order_count']},
        'restored-followup': {**base, 'group_by': 'region', 'metrics': ['order_count']},
        'month': {**base, 'group_by': 'month'},
        'empty': {**base, 'state': 'empty_result', 'operation': 'summarize', 'baseline': None,
            'current': {'start': '2016-11-01', 'end': '2016-12-01'}, 'metrics': ['sales_amount']},
        'outside': {**base, 'state': 'empty_result', 'operation': 'summarize', 'baseline': None,
            'current': {'start': '2020-01-01', 'end': '2020-02-01'}, 'metrics': ['sales_amount']},
        'clarify': {'state': 'clarification_required'}, 'quantity': {'state': 'clarification_required'},
        'clarified': {**base, 'group_by': 'region'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=DIRECTORY)
    args = parser.parse_args()
    reference, expected = Reference(), expectations()
    reports = []
    for file in sorted((args.evidence / 'cases').glob('*.result.json')):
        name = file.name.removesuffix('.result.json')
        case = read(file)
        reference.verify(case['response'], expected[name])
        reports.append({'case': name, 'equal': True, 'historical': case['historical'], 'request_id': case['body']['request_id']})
    assert len(reports) >= 10
    report = {'method': 'fresh raw CSV + Decimal, handwritten expected conditions; no production metrics/normalizer',
              'cases': reports, 'all_equal': True}
    create_once(args.evidence / ('reference-' + uuid.uuid4().hex[:10] + '.json'), report)
    print({'cases_checked': len(reports), 'all_equal': True})


if __name__ == '__main__':
    with localcontext() as context:
        context.prec = 50
        main()
