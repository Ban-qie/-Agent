"""Build the reviewed V4 source release from one clean Git commit."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ID = '55d83079902eb6387b886abac02a38d22148ea24a4e9cc384dca0b0094f18d3f'
SNAPSHOT_FILES = {
    'orders.parquet': '00dd35e2b5491739f634bcc861d0905646161a7d3f7cd84657700ec2653ac95a',
    'snapshot.json': '75514fdbd25658420277be5c75e85620204f0eacbfd516b3af75ce0fe605b72a',
}
ROOT_FILES = {
    '.dockerignore', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'MANIFEST.in',
    'pyproject.toml', 'uv.lock', 'package.json', 'yarn.lock', 'index.html',
    'tsconfig.json', 'vite.config.ts', 'eslint.config.js',
}
DEPLOY_FILES = {
    'deploy/ecommerce/.dockerignore', 'deploy/ecommerce/.env.v4.example',
    'deploy/ecommerce/Caddyfile', 'deploy/ecommerce/Dockerfile.production',
    'deploy/ecommerce/compose.yml', 'deploy/ecommerce/gunicorn.conf.py',
    'deploy/ecommerce/OPERATIONS.md', 'deploy/ecommerce/README.md',
    'deploy/ecommerce/requirements.production.lock',
}
TOOL_FILES = {
    'devtools/backup_ecommerce.py', 'devtools/restore_ecommerce.py',
    'devtools/v4_backup_common.py',
}
PREFIXES = ('public/', 'src/', 'py-src/')
PROHIBITED_PARTS = {'.git', '.local', '.venv', 'node_modules', '.env.v4.private'}
PROHIBITED_PREFIXES = ('docs/verification/', 'data/raw/')


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def git(*args):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, check=True)
    return result.stdout


def selected_tracked_path(path):
    return path in ROOT_FILES or path in DEPLOY_FILES or path in TOOL_FILES or path.startswith(PREFIXES)


def assert_safe_path(path):
    value = PurePosixPath(path)
    normalized = value.as_posix()
    if (value.is_absolute() or '..' in value.parts
            or any(part in PROHIBITED_PARTS for part in value.parts)
            or normalized.startswith(PROHIBITED_PREFIXES)):
        raise ValueError('Unsafe release path')


def committed_files(commit):
    names = git('ls-tree', '-r', '--name-only', commit).decode('utf-8').splitlines()
    selected = sorted(path for path in names if selected_tracked_path(path))
    missing = sorted((ROOT_FILES | DEPLOY_FILES | TOOL_FILES) - set(selected))
    if missing:
        raise ValueError('Required committed release files are missing: ' + ', '.join(missing))
    return selected


def add_tar_file(archive, path, payload):
    info = tarfile.TarInfo(path)
    info.size = len(payload)
    info.mode = 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    archive.addfile(info, io.BytesIO(payload))


def build_release(output_root, commit='HEAD'):
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    dirty = git('status', '--porcelain=v1', '--untracked-files=all').decode('utf-8').splitlines()
    if dirty:
        raise ValueError('Release packaging requires a clean Git worktree')
    commit = git('rev-parse', '--verify', commit + '^{commit}').decode('ascii').strip()
    name = 'ecominsight-v4-' + commit[:12]
    archive_path = output_root / (name + '.tar.gz')
    if archive_path.exists():
        raise ValueError('Release archive already exists')

    payloads = {}
    for path in committed_files(commit):
        assert_safe_path(path)
        payloads[path] = git('show', commit + ':' + path)

    snapshot_root = ROOT / 'data/processed/olist' / SNAPSHOT_ID
    for filename, expected in SNAPSHOT_FILES.items():
        source = snapshot_root / filename
        payload = source.read_bytes()
        if sha256(payload) != expected:
            raise ValueError('Snapshot file hash mismatch: ' + filename)
        path = 'data/processed/olist/' + SNAPSHOT_ID + '/' + filename
        assert_safe_path(path)
        payloads[path] = payload

    files = {path: {'bytes': len(payload), 'sha256': sha256(payload)}
             for path, payload in sorted(payloads.items())}
    manifest = {
        'format_version': 1,
        'release': 'V4',
        'source_commit': commit,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'rebuild': 'python -m devtools.v4_release_package --commit ' + commit + ' --output-root <empty-directory>',
        'runtime': {
            'python': '3.11-slim-bookworm', 'node_builder': '20-bookworm-slim',
            'gunicorn': '23.0.0', 'postgresql': '16-alpine',
            'redis': '8.8.2', 'caddy': '2.10.2-alpine',
        },
        'licenses': {
            'application': 'MIT',
            'olist_snapshot': 'CC BY-NC-SA 4.0; noncommercial aggregate demonstration only',
        },
        'snapshot': {'id': SNAPSHOT_ID, 'files': SNAPSHOT_FILES},
        'migration_scope': 'V3 multiuser data only; V1/V2 local history excluded; sessions revoked',
        'credentials_included': False,
        'private_business_data_included': False,
        'files': files,
    }
    manifest_payload = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode('utf-8')
    payloads['RELEASE-MANIFEST.json'] = manifest_payload

    with tarfile.open(archive_path, 'x:gz', format=tarfile.PAX_FORMAT) as archive:
        for path, payload in sorted(payloads.items()):
            add_tar_file(archive, name + '/' + path, payload)
    return {
        'status': 'passed', 'source_commit': commit,
        'archive': str(archive_path), 'archive_bytes': archive_path.stat().st_size,
        'archive_sha256': sha256(archive_path.read_bytes()),
        'release_root': name, 'file_count': len(payloads),
        'manifest_sha256': sha256(manifest_payload), 'model_calls': 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', default='HEAD')
    parser.add_argument('--output-root', type=Path, default=ROOT / '.local/v4-release')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    result = build_release(args.output_root, args.commit)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        with args.evidence.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
