"""Read-only V1 final integrity checks; preserves data, locks, and cost history."""
import hashlib
import json
import subprocess
from devtools.run_local import ROOT
from devtools.qwen_config import read_user_key
from devtools.v06_verify import main as verify_base


def main():
    verify_base('V1-15', include_dist=True)
    key = read_user_key().encode()
    files = {ROOT / name for name in subprocess.check_output(
        ['git', 'ls-files', '-c', '-o', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0') if name}
    files.update((ROOT / 'docs/verification').glob('V1-*.json'))
    files.update((ROOT / '.local/runtime').rglob('v1-session-state.json'))
    files.update((ROOT / '.local/runtime').rglob('session_state.json'))
    files = {file for file in files if file.is_file()}
    assert not any(key in file.read_bytes() for file in files), 'Secret scan failed; values withheld'
    for name in ['.local/verification/qwen-usage.json', '.local/runtime', 'docs/verification/V1-15-reference.json',
                 'data/raw/olist-v2/olist_orders_dataset.csv', 'data/processed/olist']:
        assert subprocess.run(['git', 'check-ignore', '-q', name], cwd=ROOT).returncode == 0
    assert not subprocess.check_output(['git', 'diff', '4a204b07', '--name-only', '--',
        'pyproject.toml', 'requirements.txt', 'uv.lock', 'package.json', 'yarn.lock'], cwd=ROOT).strip()
    ledger = ROOT / '.local/verification/qwen-usage.json'
    rows = json.loads(ledger.read_text(encoding='utf-8'))
    assert [r['attempt'] for r in rows] == list(range(1, len(rows) + 1))
    assert sum(max(r['reserved_cny'], r.get('estimated_cny', 0)) for r in rows) <= 10
    front = json.loads((ROOT / '.local/verification/v115-frontend.json').read_text(encoding='utf-8'))
    back = json.loads((ROOT / 'docs/verification/V1-15-regression.json').read_text(encoding='utf-8'))
    reference = json.loads((ROOT / 'docs/verification/V1-15-reference.json').read_text(encoding='utf-8'))
    assert front['success'] and back['exit_code'] == 0 and reference['all_equal']
    report = {'tracked_and_workspace_files_scanned': len(files), 'secret_matches': 0,
              'frontend_passed': front['numPassedTests'], 'backend_passed': back['passed'],
              'ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest(),
              'total_attempts': len(rows), 'attempts_with_usage': sum('input_tokens' in r for r in rows),
              'input_tokens': sum(r.get('input_tokens', 0) for r in rows),
              'output_tokens': sum(r.get('output_tokens', 0) for r in rows),
              'estimated_cny': sum(r.get('estimated_cny', 0) for r in rows),
              'reserved_cny': sum(r['reserved_cny'] for r in rows),
              'new_attempts_since_v114': len(rows) - 35,
              'new_estimated_cny': sum(r.get('estimated_cny', 0) for r in rows[35:]),
              'new_reserved_cny': sum(r['reserved_cny'] for r in rows[35:]),
              'ignored_private_data_and_ledgers': True, 'dependency_changes': []}
    (ROOT / 'docs/verification/V1-15-final-checks.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
