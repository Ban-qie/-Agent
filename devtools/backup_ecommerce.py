"""Create an atomic V4 PostgreSQL and immutable-snapshot backup directory."""
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

from devtools.v4_backup_common import (SCHEMA_RE, confined, database_inventory,
                                        file_manifest, sha256_file)

ADVISORY_LOCK = 846302905


def run_backup(output, *, backup_root, snapshot_root, env_file, container, schema=None):
    backup_root = Path(backup_root).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    output = confined(output, backup_root)
    if output.exists():
        raise ValueError('Backup output already exists')
    snapshot_root = Path(snapshot_root).resolve()
    if not snapshot_root.is_dir():
        raise ValueError('Snapshot root is unavailable')
    config = dotenv_values(Path(env_file).resolve())
    required = ('V4_POSTGRES_DB', 'V4_POSTGRES_USER', 'V4_POSTGRES_PASSWORD', 'V4_POSTGRES_PORT')
    if any(not config.get(key) for key in required):
        raise ValueError('Private PostgreSQL configuration is incomplete')
    schema = schema or config.get('V4_POSTGRES_SCHEMA')
    if not isinstance(schema, str) or not SCHEMA_RE.fullmatch(schema):
        raise ValueError('Invalid backup schema')

    temporary = output.parent / ('.' + output.name + '.tmp-' + uuid.uuid4().hex)
    temporary.mkdir()
    archive = temporary / 'database.dump'
    snapshot_target = temporary / 'snapshot'
    connection = None
    try:
        connection = psycopg2.connect(
            host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
            dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
            password=config['V4_POSTGRES_PASSWORD'], connect_timeout=2)
        connection.set_session(isolation_level='REPEATABLE READ', readonly=True, autocommit=False)
        cursor = connection.cursor()
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', (ADVISORY_LOCK,))
        cursor.execute('SELECT pg_export_snapshot()')
        snapshot_id = cursor.fetchone()[0]
        inventory = database_inventory(cursor, schema)
        active = sum(inventory.get('tasks', {}).get('rows', 0) for _ in [0])
        cursor.execute('SELECT count(*) FROM "' + schema + '".tasks '
                       "WHERE status IN ('accepted','running')")
        active = cursor.fetchone()[0]
        with archive.open('wb') as stream:
            process = subprocess.run([
                'docker', 'exec', container, 'pg_dump',
                '--username', config['V4_POSTGRES_USER'],
                '--dbname', config['V4_POSTGRES_DB'], '--format=custom',
                '--no-owner', '--no-privileges', '--lock-wait-timeout=5s',
                '--snapshot', snapshot_id, '--schema', schema,
            ], stdout=stream, stderr=subprocess.PIPE, timeout=120, check=False)
        if process.returncode or not archive.is_file() or archive.stat().st_size == 0:
            raise RuntimeError('PostgreSQL backup did not complete')
        shutil.copytree(snapshot_root, snapshot_target)
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                                text=True, timeout=10, check=True).stdout.strip()
        manifest = {
            'format_version': 1,
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'source': {
                'database': config['V4_POSTGRES_DB'], 'schema': schema,
                'postgres_container': container, 'postgres_volume': 'agent_ecommerce_v4_postgres_data',
                'git_commit': commit,
            },
            'database': {
                'file': 'database.dump', 'bytes': archive.stat().st_size,
                'sha256': sha256_file(archive), 'tables': inventory,
                'active_tasks_at_snapshot': active,
            },
            'snapshot': {'directory': 'snapshot', 'files': file_manifest(snapshot_target)},
            'redis': {'backed_up': False, 'restore': 'new namespace; sessions revoked'},
            'credentials_included': False,
            'source_hashes': {
                'deploy/ecommerce/compose.yml': sha256_file(ROOT / 'deploy/ecommerce/compose.yml'),
                'py-src/data_formulator/ecommerce/postgres_store.py':
                    sha256_file(ROOT / 'py-src/data_formulator/ecommerce/postgres_store.py'),
            },
        }
        manifest_path = temporary / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        (temporary / 'manifest.sha256').write_text(sha256_file(manifest_path) + '  manifest.json\n',
                                                   encoding='ascii')
        connection.rollback()
        connection.close()
        connection = None
        temporary.replace(output)
        return manifest
    except BaseException:
        if connection is not None:
            connection.rollback()
            connection.close()
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup-root', type=Path, default=ROOT / '.local/v4-backups')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--snapshot-root', type=Path, default=ROOT / 'data/processed/olist')
    parser.add_argument('--env-file', type=Path, default=ROOT / 'deploy/ecommerce/.env.v4.private')
    parser.add_argument('--container', default='agent-ecommerce-v4-postgres-1')
    parser.add_argument('--schema')
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else args.backup_root / args.output
    manifest = run_backup(output, backup_root=args.backup_root,
                          snapshot_root=args.snapshot_root, env_file=args.env_file,
                          container=args.container, schema=args.schema)
    print(json.dumps({'status': 'passed', 'output': str(Path(output).resolve()),
                      'manifest_sha256': sha256_file(Path(output).resolve() / 'manifest.json'),
                      'database_bytes': manifest['database']['bytes'],
                      'model_calls': 0}, indent=2))


if __name__ == '__main__':
    main()
