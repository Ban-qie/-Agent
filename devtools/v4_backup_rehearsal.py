"""Actual isolated A/B backup and new-database restore rehearsal for V4-S03c."""
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

from devtools.backup_ecommerce import run_backup
from devtools.restore_ecommerce import run_restore
from devtools.service_resilience import ResilienceFixture, file_hash
from devtools.v4_backup_common import database_inventory, rows_digest, sha256_file
from data_formulator.ecommerce.website_usage import WebsiteUsage


def next_output():
    directory = ROOT / 'docs/verification/V4-S03'
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for path in directory.glob('restore-rehearsal-attempt*.json'):
        try:
            attempts.append(int(path.stem.rsplit('attempt', 1)[1]))
        except (IndexError, ValueError):
            pass
    return directory / f'restore-rehearsal-attempt{max(attempts, default=0) + 1}.json'


def make_task(store, owner, request_id, state, *, unknown_usage=False, node_status=None):
    body = {'request_id': request_id, 'user_question': 'isolated restore fixture'}
    task, created = store.create_or_get(owner, 'ecommerce-v0', body)
    if not created:
        raise AssertionError('Fixture request ID was not unique')
    if state == 'accepted':
        return task
    claimed = store.claim(task['id'])
    usage = WebsiteUsage(store, initialize=False)
    reservation = usage.reserve(claimed, 1)
    usage.finish(reservation, None if unknown_usage else {'input_tokens': 1, 'output_tokens': 1})
    response = {'state': state, 'error': {'code': 'SYNTHETIC_FAILURE',
                                         'message': 'Synthetic failure'}} if state == 'failed' else {
        'state': 'success', 'result': {'answer': request_id}}
    node = {
        'node_id': request_id, 'question': body['user_question'], 'conditions': {},
        'status': node_status or state, 'result': response if state == 'success' else {},
        'parent_node_id': None, 'chart_spec': {},
        'error': response.get('error', {}),
    }
    store.finish_if_owner_version(claimed, response, node)
    return store.get_authorized(owner, 'ecommerce-v0', task['id'])


def main():
    output = next_output()
    token = uuid.uuid4().hex[:10]
    backup_root = ROOT / '.local/v4-backups'
    backup = backup_root / ('s03c-' + token)
    restore_root = ROOT / '.local/v4-restores'
    target = 'v4_restore_' + token
    namespace = 'ecommerce:v4:restore:' + token + ':'
    ledger = ROOT / '.local/verification/qwen-usage.json'
    ledger_before = file_hash(ledger)
    started = time.time()
    fixture = ResilienceFixture(keep_on_failure=True)
    config = dotenv_values(ROOT / 'deploy/ecommerce/.env.v4.private')
    result = {}
    try:
        with fixture:
            source_schema = config['V4_POSTGRES_SCHEMA']
            with fixture.store.transaction() as database:
                database.execute('CREATE TABLE legacy_debug_archive ('
                                 'id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL,payload BYTEA NOT NULL)')
                database.execute('INSERT INTO legacy_debug_archive '
                                 'SELECT id,sha256,payload FROM "' + source_schema + '".legacy_debug_archive')
                for index, owner in enumerate(fixture.owners.values(), start=1):
                    database.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)',
                                     (f'session-{index}', owner, 1, time.time() + 1800))

            for username, owner in fixture.owners.items():
                make_task(fixture.store, owner, f'{username}-success-001', 'success')
                make_task(fixture.store, owner, f'{username}-failure-001', 'failed',
                          unknown_usage=True, node_status='failed')
            make_task(fixture.store, fixture.owners['alice'], 'alice-active-001', 'accepted')
            active = make_task(fixture.store, fixture.owners['bobby'], 'bobby-active-001', 'accepted')
            fixture.store.claim(active['id'])

            with fixture.store.transaction() as database:
                source_inventory = database_inventory(database.cursor, fixture.identity.schema)
                archive = database.execute('SELECT sha256,payload FROM legacy_debug_archive WHERE id=1').fetchone()
                archive_hash = archive['sha256']
                archive_payload_hash = __import__('hashlib').sha256(bytes(archive['payload'])).hexdigest()
                unknown_usage = database.execute(
                    'SELECT count(*) FROM website_usage WHERE settlement LIKE ?',
                    ('%unknown%',)).fetchone()[0]
                active_before = database.execute(
                    "SELECT count(*) FROM tasks WHERE status IN ('accepted','running')").fetchone()[0]
                sessions_before = database.execute('SELECT count(*) FROM auth_sessions').fetchone()[0]

            backup_started = time.perf_counter()
            manifest = run_backup(backup, backup_root=backup_root,
                                  snapshot_root=ROOT / 'data/processed/olist',
                                  env_file=ROOT / 'deploy/ecommerce/.env.v4.private',
                                  container='agent-ecommerce-v4-postgres-1',
                                  schema=fixture.identity.schema)
            backup_seconds = time.perf_counter() - backup_started
            restore_started = time.perf_counter()
            receipt = run_restore(backup, backup_root=backup_root, restore_root=restore_root,
                                  target=target, env_file=ROOT / 'deploy/ecommerce/.env.v4.private',
                                  container='agent-ecommerce-v4-postgres-1',
                                  redis_namespace=namespace)
            restore_seconds = time.perf_counter() - restore_started

            restored = psycopg2.connect(host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
                                        dbname=target, user=config['V4_POSTGRES_USER'],
                                        password=config['V4_POSTGRES_PASSWORD'], connect_timeout=2)
            cursor = restored.cursor()
            cursor.execute('SELECT status,count(*) FROM "' + fixture.identity.schema +
                           '".tasks GROUP BY status ORDER BY status')
            task_statuses = dict(cursor.fetchall())
            cursor.execute('SELECT count(*) FROM "' + fixture.identity.schema + '".auth_sessions')
            sessions_after = cursor.fetchone()[0]
            cursor.execute('SELECT sha256,payload FROM "' + fixture.identity.schema +
                           '".legacy_debug_archive WHERE id=1')
            restored_archive = cursor.fetchone()
            cursor.execute('SELECT count(*) FROM "' + fixture.identity.schema +
                           '".website_usage WHERE settlement LIKE %s', ('%unknown%',))
            restored_unknown = cursor.fetchone()[0]
            restored.close()
            if (active_before != 2 or receipt['interrupted_tasks'] != 2
                    or task_statuses.get('interrupted') != 2 or sessions_before != 2
                    or sessions_after != 0 or restored_unknown != unknown_usage
                    or restored_archive[0] != archive_hash
                    or __import__('hashlib').sha256(bytes(restored_archive[1])).hexdigest() != archive_payload_hash
                    or manifest['database']['tables'] != source_inventory):
                raise AssertionError('Restored A/B state did not satisfy the frozen policy')
            result = {
                'status': 'passed', 'step': 'V4-S03c',
                'utc': datetime.now(timezone.utc).isoformat(),
                'source_schema': fixture.identity.schema, 'target_database': target,
                'backup_directory': str(backup.relative_to(ROOT)),
                'restore_directory': str((restore_root / target).relative_to(ROOT)),
                'backup_manifest_sha256': sha256_file(backup / 'manifest.json'),
                'database_archive_sha256': manifest['database']['sha256'],
                'database_archive_bytes': manifest['database']['bytes'],
                'source_inventory': source_inventory,
                'task_statuses_after': task_statuses,
                'active_tasks_before': active_before, 'interrupted_tasks_after': 2,
                'sessions_before': sessions_before, 'sessions_after': sessions_after,
                'unknown_usage_before': unknown_usage, 'unknown_usage_after': restored_unknown,
                'legacy_archive_sha256': archive_hash,
                'legacy_archive_payload_sha256': archive_payload_hash,
                'backup_seconds': round(backup_seconds, 3),
                'restore_seconds': round(restore_seconds, 3),
                'rto_seconds': round(backup_seconds + restore_seconds, 3),
                'rto_objective_seconds': 1800,
                'redis_namespace_keys': receipt['redis_keys'],
                'model_dispatches': receipt['model_dispatches'], 'qwen_calls': 0,
                'qwen_ledger_sha256_before': ledger_before,
            }
    except Exception as error:
        result = {'status': 'failed', 'step': 'V4-S03c',
                  'error_type': type(error).__name__, 'qwen_calls': 0,
                  'qwen_ledger_sha256_before': ledger_before,
                  'backup_directory': str(backup.relative_to(ROOT)),
                  'target_database': target}
        raise
    finally:
        admin = psycopg2.connect(host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
                                 dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
                                 password=config['V4_POSTGRES_PASSWORD'], connect_timeout=2)
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                           'WHERE datname=%s AND pid<>pg_backend_pid()', (target,))
            cursor.execute('DROP DATABASE IF EXISTS "' + target + '"')
        admin.close()
        result['target_database_cleaned'] = True
        result['fixture_cleanup'] = fixture.cleanup
        result['qwen_ledger_sha256_after'] = file_hash(ledger)
        result['ledger_unchanged'] = result['qwen_ledger_sha256_after'] == ledger_before
        result['total_seconds'] = round(time.time() - started, 3)
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(output), **result}, indent=2))


if __name__ == '__main__':
    main()
