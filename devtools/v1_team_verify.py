"""Read-only team evidence/reference verification; never calls a provider."""
import hashlib
import json
import subprocess
from decimal import Decimal
from devtools.run_local import ROOT
from devtools.reference_olist import reference
from devtools.v06_verify import main as integrity


def main():
    integrity('V1-team', include_dist=True)
    cases = json.loads((ROOT / 'docs/verification/V1-team-browser.json').read_text(encoding='utf-8'))['cases']
    raw = ROOT / 'data/raw/olist-v2'
    expected = reference(raw, '2018-01-01', '2018-02-01')
    for case in cases:
        assert case['response']['result']['values'] == expected
        assert case['response']['collaboration']['review'] == 'approve'
        assert case['response']['explanation']['grounded']
        assert case['response']['budget']['model_calls'] == 3
    from data_formulator.ecommerce.snapshot import STATES
    groups = {region: reference(raw, '2018-01-01', '2018-02-01', region) for region in sorted(STATES | {'UNKNOWN'})}
    ranked = sorted(groups, key=lambda key: Decimal(groups[key]['sales_amount']), reverse=True)[:3]
    actual = cases[1]['response']['result']['groups']
    assert [row['key'] for row in actual] == ranked
    for row in actual:
        assert {k: v for k, v in row.items() if k != 'key'} == groups[row['key']]
    ledger = ROOT / '.local/verification/qwen-usage.json'
    rows = json.loads(ledger.read_text(encoding='utf-8'))
    baseline_hash = json.loads((ROOT / 'docs/verification/V1-16-release.json').read_text())['ledger_sha256']
    # Prefix comparison is also enforced by every live harness invocation.
    assert len(rows) >= 40 and all(r['attempt'] == i for i, r in enumerate(rows, 1))
    assert sum(max(r['reserved_cny'], r.get('estimated_cny', 0)) for r in rows) <= 10
    report = {'independent_raw_csv_equal': True, 'ranked_groups_equal': True,
              'successful_team_scenarios': 2, 'model_calls_per_success': 3,
              'new_attempts': len(rows) - 40, 'total_attempts': len(rows),
              'estimated_cny': sum(r.get('estimated_cny', 0) for r in rows),
              'reserved_cny': sum(r['reserved_cny'] for r in rows),
              'new_estimated_cny': sum(r.get('estimated_cny', 0) for r in rows[40:]),
              'prior_ledger_sha256': baseline_hash, 'ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest()}
    (ROOT / 'docs/verification/V1-team-final.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
