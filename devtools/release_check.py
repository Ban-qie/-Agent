"""Read-only validation of the exact staged source tree; reports no secret values."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

from devtools.run_local import ROOT


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main():
    from devtools.qwen_config import read_user_key
    key = read_user_key().encode()
    paths = [p for p in git('ls-files', '-z').decode().split('\0') if p]
    forbidden = ('.local/', '.venv/', 'node_modules/', 'data/raw/', 'data/processed/', 'docs/verification/')
    invalid = [p for p in paths if p.startswith(forbidden) or re.match(r'docs/V0-.*\.md$', p)]
    assert not invalid, 'Private paths present in the index'
    matches, suspects, fingerprints = [], [], {}
    signatures = [rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                  rb'(?<![A-Za-z0-9])(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}',
                  rb'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{32,}']
    for name in paths:
        content = git('show', ':' + name)
        if key in content:
            matches.append(name)
        if any(re.search(pattern, content) for pattern in signatures):
            suspects.append(name)
        fingerprints[name] = hashlib.sha256(content).hexdigest()
    # Only counts are printed; neither values nor matching lines leave this check.
    assert not matches, 'Exact credential match in staged tree'
    assert not suspects, 'Potential private credential in staged tree; inspect privately'
    assert git('show', ':LICENSE') == git('show', '5477f0e:LICENSE')
    assert not git('diff', '--cached', '--name-only', '--', 'pyproject.toml', 'uv.lock', 'package.json', 'yarn.lock').strip()
    subprocess.run(['git', 'diff', '--cached', '--check'], cwd=ROOT, check=True)
    import yaml
    for name in ('.github/workflows/python-build.yml', '.github/workflows/desktop-build.yml'):
        workflow = yaml.safe_load(git('show', ':' + name))
        assert all("github.repository == 'microsoft/data-formulator'" in job['if'] for job in workflow['jobs'].values())
    ledger = ROOT / '.local/verification/qwen-usage.json'
    assert json.loads(ledger.read_text()) == json.loads((ROOT / 'docs/verification/V0-9-usage.json').read_text())
    for name in ('.env', '.env.local', '.local/verification/qwen-usage.json',
                 'data/raw/olist-v2/olist_orders_dataset.csv', 'data/processed/olist',
                 'docs/verification/V0-9-usage.json', 'docs/V0-交接记录.md'):
        subprocess.run(['git', 'check-ignore', '-q', name], cwd=ROOT, check=True)
    summary = {'staged_tree': git('write-tree').decode().strip(), 'files_scanned': len(paths),
               'exact_key_matches': 0, 'credential_pattern_matches': 0, 'private_paths': [],
               'upstream_license_unchanged': True, 'dependency_locks_unchanged': True,
               'upstream_workflow_jobs_guarded': True, 'ledger_unchanged_since_v09': True,
               'ledger_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest()}
    output = ROOT / '.local/verification'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'v010-staged-files.json').write_text(json.dumps(fingerprints, indent=2), encoding='utf-8')
    (output / 'v010-release-check.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
