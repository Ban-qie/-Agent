"""Prove corrupt V4 backups are rejected without changing the source database."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time
import uuid

import psycopg2
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'py-src'))

from devtools.restore_ecommerce import run_restore
from devtools.service_resilience import file_hash
from devtools.v4_backup_common import database_inventory


def next_output():
    directory = ROOT / 'docs/verification/V4-S03'
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for path in directory.glob('corrupt-restore-attempt*.json'):
        try:
            attempts.append(int(path.stem.rsplit('attempt', 1)[1]))
        except (IndexError, ValueError):
            pass
    return directory / f'corrupt-restore-attempt{max(attempts, default=0) + 1}.json'


def source_inventory(config):
    connection = psycopg2.connect(host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
                                  dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
                                  password=config['V4_POSTGRES_PASSWORD'], connect_timeout=2)
    try:
        cursor = connection.cursor()
        return database_inventory(cursor, config['V4_POSTGRES_SCHEMA'])
    finally:
        connection.close()


def target_exists(config, target):
    connection = psycopg2.connect(host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
                                  dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
                                  password=config['V4_POSTGRES_PASSWORD'], connect_timeout=2)
    connection.autocommit = True
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT 1 FROM pg_database WHERE datname=%s', (target,))
        return cursor.fetchone() is not None
    finally:
        connection.close()


def main():
    output = next_output()
    config = dotenv_values(ROOT / 'deploy/ecommerce/.env.v4.private')
    candidates = sorted((ROOT / '.local/v4-backups').glob('s03c-*'),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit('No passed S03c backup is available')
    source = candidates[0]
    token = uuid.uuid4().hex[:8]
    root = ROOT / '.local/v4-backups' / ('s03d-corrupt-' + token)
    root.mkdir(parents=True)
    baseline = source_inventory(config)
    ledger = ROOT / '.local/verification/qwen-usage.json'
    ledger_before = file_hash(ledger)
    started = time.time()
    results = []
    cases = ('missing-manifest', 'changed-manifest', 'changed-archive',
             'missing-archive', 'missing-snapshot')
    try:
        for index, case in enumerate(cases, start=1):
            candidate = root / case
            shutil.copytree(source, candidate)
            if case == 'missing-manifest':
                (candidate / 'manifest.json').unlink()
            elif case == 'changed-manifest':
                with (candidate / 'manifest.json').open('ab') as stream:
                    stream.write(b' ')
            elif case == 'changed-archive':
                archive = candidate / 'database.dump'
                with archive.open('r+b') as stream:
                    first = stream.read(1)
                    stream.seek(0)
                    stream.write(bytes([first[0] ^ 1]))
            elif case == 'missing-archive':
                (candidate / 'database.dump').unlink()
            elif case == 'missing-snapshot':
                snapshot = next((candidate / 'snapshot').rglob('orders.parquet'))
                snapshot.unlink()
            target = f'v4_restore_corrupt_{token}_{index}'
            began = time.perf_counter()
            try:
                run_restore(candidate, backup_root=ROOT / '.local/v4-backups',
                            restore_root=ROOT / '.local/v4-restores', target=target,
                            env_file=ROOT / 'deploy/ecommerce/.env.v4.private',
                            container='agent-ecommerce-v4-postgres-1',
                            redis_namespace=f'ecommerce:v4:restore:{token}{index}:')
            except ValueError as error:
                rejected = True
                error_type = type(error).__name__
            else:
                rejected = False
                error_type = None
            current = source_inventory(config)
            exists = target_exists(config, target)
            result = {
                'case': case, 'rejected': rejected, 'error_type': error_type,
                'seconds': round(time.perf_counter() - began, 3),
                'target_database_created': exists,
                'source_inventory_unchanged': current == baseline,
            }
            results.append(result)
            if not rejected or exists or current != baseline:
                raise AssertionError(result)
        report = {
            'status': 'passed', 'step': 'V4-S03d',
            'utc': datetime.now(timezone.utc).isoformat(),
            'source_backup': str(source.relative_to(ROOT)),
            'cases': results, 'rejected_cases': len(results),
            'source_inventory': baseline, 'model_dispatches': 0, 'qwen_calls': 0,
            'qwen_ledger_sha256_before': ledger_before,
        }
    except Exception as error:
        report = {'status': 'failed', 'step': 'V4-S03d',
                  'error_type': type(error).__name__, 'completed_cases': results,
                  'model_dispatches': 0, 'qwen_calls': 0,
                  'qwen_ledger_sha256_before': ledger_before}
        raise
    finally:
        report['qwen_ledger_sha256_after'] = file_hash(ledger)
        report['ledger_unchanged'] = report['qwen_ledger_sha256_after'] == ledger_before
        report['duration_seconds'] = round(time.time() - started, 3)
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(output), **report}, indent=2))


if __name__ == '__main__':
    main()
