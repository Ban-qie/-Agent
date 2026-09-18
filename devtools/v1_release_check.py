"""Check the exact V1 staged release tree without model calls or history rewrites."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

from devtools.run_local import ROOT
from devtools.qwen_config import read_user_key


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main():
    key = read_user_key().encode()
    signatures = [rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                  rb'(?<![A-Za-z0-9])(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}',
                  rb'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{32,}']
    paths = [p for p in git('ls-files', '-z').decode().split('\0') if p]
    private = ('.local/', '.venv/', 'node_modules/', 'data/raw/', 'data/processed/', 'docs/verification/')
    assert not [p for p in paths if p.startswith(private) or re.match(r'docs/V[01]-(?!architecture-contract\.md$).*\.md$', p)]
    fingerprints = {}
    for name in paths:
        content = git('show', ':' + name)
        assert key not in content, 'Exact credential match (value withheld)'
        assert not any(re.search(pattern, content) for pattern in signatures), 'Credential pattern match (value withheld)'
        fingerprints[name] = hashlib.sha256(content).hexdigest()
    assert git('show', ':LICENSE') == git('show', 'v0.1.0:LICENSE')
    assert not git('diff', '--cached', 'd6d8f3cb', '--name-only', '--', 'py-src', 'src', 'tests',
                   'pyproject.toml', 'requirements.txt', 'uv.lock', 'package.json', 'yarn.lock').strip()
    import yaml
    for name in ('.github/workflows/python-build.yml', '.github/workflows/desktop-build.yml'):
        workflow = yaml.safe_load(git('show', ':' + name))
        assert all("github.repository == 'microsoft/data-formulator'" in job['if'] for job in workflow['jobs'].values())
    for name in ('README.md', 'docs/ECOMMERCE_V1.md', 'docs/RELEASE_V1.md'):
        content = git('show', ':' + name).decode()
        assert '????' not in content
        # Check the project introduction only, leaving upstream README links intact.
        content = content.split('<h1 align=')[0]
        for target in re.findall(r'\]\(([^)]+)\)', content):
            if '://' not in target and not target.startswith('#'):
                assert (ROOT / Path(name).parent / target.split('#')[0]).exists(), target
    ledger = ROOT / '.local/verification/qwen-usage.json'
    ledger_hash = hashlib.sha256(ledger.read_bytes()).hexdigest()
    accepted = json.loads((ROOT / 'docs/verification/V1-15-final-checks.json').read_text())
    assert ledger_hash == accepted['ledger_sha256']
    summary = json.loads(git('show', ':docs/validation/V1-summary.json'))
    assert summary['backend_passed'] == accepted['backend_passed'] == 264
    assert summary['frontend_passed'] == accepted['frontend_passed'] == 15
    # Scan only locally added V1 history, not unavailable upstream partial-clone objects.
    objects = git('rev-list', '--objects', '--no-object-names', 'v0.1.0..HEAD').splitlines()
    wire = subprocess.check_output(['git', 'cat-file', '--batch'], input=b'\n'.join(objects) + b'\n', cwd=ROOT)
    offset = blobs = 0
    while offset < len(wire):
        end = wire.index(b'\n', offset)
        oid, kind, size = wire[offset:end].split()
        size = int(size)
        content = wire[end + 1:end + 1 + size]
        offset = end + 2 + size
        if kind == b'blob':
            blobs += 1
            assert key not in content, 'Credential in local V1 history'
            assert not any(re.search(pattern, content) for pattern in signatures), 'Credential pattern in local V1 history'
    subprocess.run(['git', 'diff', '--cached', '--check'], cwd=ROOT, check=True)
    report = {'staged_tree': git('write-tree').decode().strip(), 'files_scanned': len(paths),
              'exact_key_matches': 0, 'credential_pattern_matches': 0, 'private_paths_in_tree': [],
              'local_v1_history_blobs_scanned': blobs, 'history_credential_matches': 0,
              'history_scope': 'Local V1 objects only; earlier private handoff documents remain in local history. No push.',
              'application_tests_dependencies_unchanged_since_v115': True, 'license_preserved': True,
              'workflow_guards_preserved': True, 'ledger_sha256': ledger_hash, 'new_model_attempts': 0}
    for name, data in [('V1-16-release-check.json', report), ('V1-16-staged-files.json', fingerprints)]:
        (ROOT / 'docs/verification' / name).write_text(json.dumps(data, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
