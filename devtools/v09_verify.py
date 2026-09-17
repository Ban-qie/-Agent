"""Final integrity, accumulated usage, and persisted-state checks for V0-9."""
import hashlib
import json
import subprocess

from devtools.qwen_config import read_user_key
from devtools.run_local import ROOT
from devtools.v06_verify import main


if __name__ == '__main__':
    main('V0-9', include_dist=True)
    inherited = json.loads((ROOT / 'docs/verification/V0-8-usage.json').read_text(encoding='utf-8'))
    ledger = ROOT / '.local/verification/qwen-usage.json'
    rows = json.loads(ledger.read_text(encoding='utf-8'))
    assert rows[:len(inherited)] == inherited
    new = rows[len(inherited):]
    assert len(new) == 8 and all('input_tokens' in row for row in new)
    assert sum(max(r['reserved_cny'], r.get('estimated_cny', 0)) for r in rows) <= 10
    key = read_user_key().encode()
    files = list((ROOT / '.local/runtime/users').rglob('session_state.json'))
    assert files and not any(key in file.read_bytes() for file in files)
    assert json.loads((ROOT / 'docs/verification/V0-9-usage.json').read_text()) == rows
    for file in files:
        state = json.loads(file.read_text(encoding='utf-8'))
        assert not any(field in state for field in ('models', 'identity', 'serverConfig', 'selectedModelId'))
    ignored = ['.local/runtime/ecommerce/analysis-audit.json', '.local/verification/qwen-usage.json',
               'data/raw/olist-v2/olist_orders_dataset.csv', 'data/processed/olist']
    for value in ignored:
        assert subprocess.run(['git', 'check-ignore', '-q', value], cwd=ROOT).returncode == 0
    frontend = json.loads((ROOT / '.local/verification/v09-frontend.json').read_text(encoding='utf-8'))
    assert frontend['success'] and frontend['numPassedTests'] == 12
    report = {'workspace_files_scanned': len(files), 'workspace_key_matches': 0,
              'inherited_ledger_prefix_equal': True, 'new_attempts': len(new),
              'input_tokens_added': sum(r['input_tokens'] for r in new),
              'output_tokens_added': sum(r['output_tokens'] for r in new),
              'estimated_cny_added': sum(r['estimated_cny'] for r in new),
              'reserved_cny_added': sum(r['reserved_cny'] for r in new),
              'total_attempts': len(rows), 'ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest(),
              'ignored_data_and_runtime': ignored, 'frontend_passed': frontend['numPassedTests'],
              'frontend_failed': frontend['numFailedTests']}
    (ROOT / 'docs/verification/V0-9-final-checks.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
