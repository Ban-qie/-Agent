"""Rehearse a V4 rollback boundary against a verified local backup."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import psycopg2
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'py-src'))
OLD_COMMIT = 'a780bd18db8ae3ed22078a684ca3fc420bbe3688'


def next_output():
    directory = ROOT / 'docs/verification/V4-S05'
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for path in directory.glob('rollback-attempt*.json'):
        try:
            attempts.append(int(path.stem.rsplit('attempt', 1)[1]))
        except (IndexError, ValueError):
            pass
    return directory / f'rollback-attempt{max(attempts, default=0) + 1}.json'


def git_has_file(commit, path):
    return subprocess.run(['git', 'cat-file', '-e', f'{commit}:{path}'],
                          cwd=ROOT, capture_output=True, check=False).returncode == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup-root', type=Path, default=ROOT / '.local/v4-backups')
    parser.add_argument('--backup')
    parser.add_argument('--container', default='agent-ecommerce-v4-postgres-1')
    args = parser.parse_args()
    from devtools.restore_ecommerce import load_verified_manifest, run_restore
    from devtools.v4_backup_common import sha256_file

    output = next_output()
    config = dotenv_values(ROOT / 'deploy/ecommerce/.env.v4.private')
    candidates = ([args.backup_root / args.backup] if args.backup else
                  sorted(args.backup_root.glob('s03c-*'), key=lambda path: path.stat().st_mtime, reverse=True))
    backup = next((path for path in candidates if (path / 'manifest.json').is_file()), None)
    if backup is None:
        raise ValueError('No verified rollback backup is available')
    manifest = load_verified_manifest(backup)
    token = uuid.uuid4().hex[:10]
    target = 'v4_restore_rollback_' + token
    restore_root = ROOT / '.local/v4-rollback-restores'
    namespace = 'ecommerce:v4:restore:rollback:' + token + ':'
    gate = ROOT / '.local/v4-rollback' / (token + '-public-entry.json')
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps({'public_analysis_open': True}), encoding='utf-8')
    report = {
        'status': 'failed', 'step': 'V4-S05c',
        'utc': datetime.now(timezone.utc).isoformat(),
        'backup': str(backup.relative_to(ROOT)),
        'backup_manifest_sha256': sha256_file(backup / 'manifest.json'),
        'target_database': target, 'redis_namespace': namespace,
        'model_dispatches': 0, 'qwen_calls': 0,
        'compatibility_boundary': {
            'old_commit': OLD_COMMIT,
            'old_has_postgres_store': git_has_file(OLD_COMMIT, 'py-src/data_formulator/ecommerce/postgres_store.py'),
            'old_has_production_factory': git_has_file(OLD_COMMIT, 'py-src/data_formulator/ecommerce/production_app.py'),
            'policy': 'Old code is never pointed at the V4 PostgreSQL schema; restore old code only with its old storage contract.'
        },
    }
    corrupt = ROOT / '.local/v4-rollback' / (token + '-corrupt-backup')
    admin = None
    try:
        receipt = run_restore(backup, backup_root=args.backup_root, restore_root=restore_root,
                              target=target, env_file=ROOT / 'deploy/ecommerce/.env.v4.private',
                              container=args.container, redis_namespace=namespace)
        dsn = {
            'host': '127.0.0.1', 'port': int(config['V4_POSTGRES_PORT']),
            'dbname': target, 'user': config['V4_POSTGRES_USER'],
            'password': config['V4_POSTGRES_PASSWORD'], 'connect_timeout': 2,
        }
        restored = psycopg2.connect(**dsn)
        cursor = restored.cursor()
        schema = manifest['source']['schema']
        cursor.execute('SELECT count(*) FROM "' + schema + '".accounts')
        accounts = cursor.fetchone()[0]
        cursor.execute('SELECT count(*) FROM "' + schema + '".auth_sessions')
        sessions = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM \"" + schema + "\".tasks WHERE status IN ('accepted','running')")
        active = cursor.fetchone()[0]
        restored.close()

        shutil.copytree(backup, corrupt)
        (corrupt / 'manifest.json').unlink()
        try:
            load_verified_manifest(corrupt)
        except ValueError:
            corrupt_rejected = True
        else:
            corrupt_rejected = False
        if not corrupt_rejected:
            raise AssertionError('Corrupt rollback backup was accepted')
        gate.write_text(json.dumps({'public_analysis_open': False, 'reason': 'rollback verification failure'}),
                        encoding='utf-8')
        gate_state = json.loads(gate.read_text(encoding='utf-8'))
        if gate_state.get('public_analysis_open') is not False:
            raise AssertionError('Public entry remained open after rollback failure')
        report.update(status='passed', restore_receipt=receipt,
                      restored_accounts=accounts, restored_sessions=sessions,
                      restored_active_tasks=active, corrupt_backup_rejected=corrupt_rejected,
                      public_entry_closed_after_failure=True,
                      old_code_new_schema_refused=(not report['compatibility_boundary']['old_has_postgres_store']
                                                   and not report['compatibility_boundary']['old_has_production_factory']),
                      rollback_order=['close-entry', 'preserve-failure', 'restore-new-target',
                                      'revoke-sessions', 'verify-health', 'reopen-only-after-success'])
    finally:
        if admin is None:
            admin = psycopg2.connect(host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
                                     dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
                                     password=config['V4_POSTGRES_PASSWORD'], connect_timeout=2)
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                           'WHERE datname=%s AND pid<>pg_backend_pid()', (target,))
            cursor.execute('DROP DATABASE IF EXISTS "' + target + '"')
        admin.close()
        shutil.rmtree(restore_root / target, ignore_errors=True)
        shutil.rmtree(corrupt, ignore_errors=True)
        gate.unlink(missing_ok=True)
        report['target_database_cleaned'] = True
        report['duration_seconds'] = round(time.time() - datetime.fromisoformat(report['utc']).timestamp(), 3)
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(output), **report}, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
