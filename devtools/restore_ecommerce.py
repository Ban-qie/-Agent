"""Verify and restore a V4 backup into a new PostgreSQL database and directory."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import psycopg2
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from devtools.v4_backup_common import (DATABASE_RE, SCHEMA_RE, confined,
                                        database_inventory, sha256_file, verify_files)


def load_verified_manifest(backup):
    backup = Path(backup).resolve()
    manifest_path = backup / 'manifest.json'
    checksum_path = backup / 'manifest.sha256'
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise ValueError('Backup manifest or checksum is missing')
    expected = checksum_path.read_text(encoding='ascii').split()[0]
    if expected != sha256_file(manifest_path):
        raise ValueError('Backup manifest checksum does not match')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('format_version') != 1 or manifest.get('credentials_included') is not False:
        raise ValueError('Unsupported backup manifest')
    archive = backup / manifest['database']['file']
    if (not archive.is_file() or archive.stat().st_size != manifest['database']['bytes']
            or sha256_file(archive) != manifest['database']['sha256']):
        raise ValueError('Database archive does not match the manifest')
    verify_files(backup / manifest['snapshot']['directory'], manifest['snapshot']['files'])
    return manifest


def run_restore(backup, *, backup_root, restore_root, target, env_file, container,
                redis_namespace):
    backup = confined(backup, backup_root, must_exist=True)
    restore_root = Path(restore_root).resolve()
    restore_root.mkdir(parents=True, exist_ok=True)
    target_root = confined(restore_root / target, restore_root)
    if target_root.exists():
        raise ValueError('Restore output already exists')
    if not DATABASE_RE.fullmatch(target):
        raise ValueError('Invalid restore database name')
    if (not isinstance(redis_namespace, str)
            or not redis_namespace.startswith('ecommerce:v4:restore:')
            or len(redis_namespace) > 64):
        raise ValueError('Restore requires a new isolated Redis namespace')
    manifest = load_verified_manifest(backup)
    schema = manifest['source']['schema']
    if not SCHEMA_RE.fullmatch(schema):
        raise ValueError('Invalid source schema in manifest')
    config = dotenv_values(Path(env_file).resolve())
    required = ('V4_POSTGRES_DB', 'V4_POSTGRES_USER', 'V4_POSTGRES_PASSWORD',
                'V4_POSTGRES_PORT', 'V4_REDIS_PASSWORD', 'V4_REDIS_PORT')
    if any(not config.get(key) for key in required):
        raise ValueError('Private restore configuration is incomplete')
    if target in {config['V4_POSTGRES_DB'], manifest['source']['database']}:
        raise ValueError('Restore target must differ from every source database')

    from data_formulator.ecommerce.redis_runtime import RedisClient
    redis = RedisClient('127.0.0.1', int(config['V4_REDIS_PORT']),
                        config['V4_REDIS_PASSWORD'], namespace=redis_namespace)
    scan = redis.execute('SCAN', 0, 'MATCH', redis_namespace + '*', 'COUNT', 1000)
    if not isinstance(scan, list) or len(scan) != 2 or scan[1]:
        raise ValueError('Restore Redis namespace is not empty')

    admin = psycopg2.connect(host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
                             dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
                             password=config['V4_POSTGRES_PASSWORD'], connect_timeout=2)
    admin.autocommit = True
    created = False
    temporary = restore_root / ('.' + target + '.tmp-' + uuid.uuid4().hex)
    try:
        with admin.cursor() as cursor:
            cursor.execute('SELECT 1 FROM pg_database WHERE datname=%s', (target,))
            if cursor.fetchone():
                raise ValueError('Restore database already exists')
        temporary.mkdir()
        process = subprocess.run([
            'docker', 'exec', container, 'createdb', '--username', config['V4_POSTGRES_USER'],
            '--template', 'template0', target,
        ], capture_output=True, timeout=30, check=False)
        if process.returncode:
            raise RuntimeError('Could not create restore database')
        created = True
        archive = backup / manifest['database']['file']
        with archive.open('rb') as stream:
            process = subprocess.run([
                'docker', 'exec', '-i', container, 'pg_restore',
                '--username', config['V4_POSTGRES_USER'], '--dbname', target,
                '--no-owner', '--no-privileges', '--exit-on-error',
            ], stdin=stream, capture_output=True, timeout=120, check=False)
        if process.returncode:
            raise RuntimeError('PostgreSQL restore did not complete')

        restored = psycopg2.connect(host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
                                    dbname=target, user=config['V4_POSTGRES_USER'],
                                    password=config['V4_POSTGRES_PASSWORD'], connect_timeout=2)
        restored.autocommit = False
        cursor = restored.cursor()
        before_policy = database_inventory(cursor, schema)
        if before_policy != manifest['database']['tables']:
            raise ValueError('Restored rows do not match the backup inventory')
        cursor.execute('DELETE FROM "' + schema + '".auth_sessions')
        cursor.execute('UPDATE "' + schema + '".tasks SET status=\'interrupted\','
                       'version=version+1,lease_token=NULL,lease_expiry=NULL,cancel_requested=0,'
                       'response=%s,updated=EXTRACT(EPOCH FROM clock_timestamp()) '
                       "WHERE status IN ('accepted','running')", ('{"state":"interrupted"}',))
        interrupted = cursor.rowcount
        restored.commit()
        after_policy = database_inventory(cursor, schema)
        restored.close()

        snapshot_target = temporary / 'snapshot'
        shutil.copytree(backup / manifest['snapshot']['directory'], snapshot_target)
        verify_files(snapshot_target, manifest['snapshot']['files'])
        receipt = {
            'status': 'passed', 'restored_utc': datetime.now(timezone.utc).isoformat(),
            'target_database': target, 'schema': schema,
            'source_manifest_sha256': sha256_file(backup / 'manifest.json'),
            'pre_policy_inventory_equal': True, 'post_policy_tables': after_policy,
            'interrupted_tasks': interrupted, 'sessions_revoked':
                before_policy.get('auth_sessions', {}).get('rows', 0),
            'redis_namespace': redis_namespace, 'redis_keys': 0,
            'model_dispatches': 0,
        }
        (temporary / 'restore-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        temporary.replace(target_root)
        return receipt
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        if created:
            with admin.cursor() as cursor:
                cursor.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                               'WHERE datname=%s AND pid<>pg_backend_pid()', (target,))
                cursor.execute('DROP DATABASE IF EXISTS "' + target + '"')
        raise
    finally:
        admin.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup-root', type=Path, default=ROOT / '.local/v4-backups')
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--restore-root', type=Path, default=ROOT / '.local/v4-restores')
    parser.add_argument('--target-database', required=True)
    parser.add_argument('--redis-namespace', required=True)
    parser.add_argument('--env-file', type=Path, default=ROOT / 'deploy/ecommerce/.env.v4.private')
    parser.add_argument('--container', default='agent-ecommerce-v4-postgres-1')
    args = parser.parse_args()
    backup = args.backup if args.backup.is_absolute() else args.backup_root / args.backup
    receipt = run_restore(backup, backup_root=args.backup_root, restore_root=args.restore_root,
                          target=args.target_database, env_file=args.env_file,
                          container=args.container, redis_namespace=args.redis_namespace)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
