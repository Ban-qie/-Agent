"""Probe the frozen one-slot invariant across independent V4 stores."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'py-src'))

from devtools.service_resilience import ResilienceFixture, file_hash


def next_output():
    directory = ROOT / 'docs/verification/V4-S04'
    attempts = []
    for path in directory.glob('multi-instance-slot-attempt*.json'):
        try:
            attempts.append(int(path.stem.rsplit('attempt', 1)[1]))
        except (IndexError, ValueError):
            pass
    return directory / f'multi-instance-slot-attempt{max(attempts, default=0) + 1}.json'


def main():
    from psycopg2.extensions import make_dsn
    from data_formulator.ecommerce.contracts import ToolError
    from data_formulator.ecommerce.postgres_store import PostgresTaskStore
    from data_formulator.ecommerce.redis_runtime import RedisClient, RedisCoordinator

    output = next_output()
    fixture = ResilienceFixture(keep_on_failure=True)
    peer = None
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = file_hash(ledger)
    report = {}
    try:
        with fixture:
            dsn = make_dsn(host='127.0.0.1', port=int(fixture.config['V4_POSTGRES_PORT']),
                           dbname=fixture.config['V4_POSTGRES_DB'],
                           user=fixture.config['V4_POSTGRES_USER'],
                           password=fixture.config['V4_POSTGRES_PASSWORD'])
            peer_redis = RedisClient('127.0.0.1', int(fixture.config['V4_REDIS_PORT']),
                                     fixture.config['V4_REDIS_PASSWORD'],
                                     namespace=fixture.identity.redis_namespace)
            peer = PostgresTaskStore(dsn, schema=fixture.identity.schema,
                                     coordinator=RedisCoordinator(peer_redis), max_connections=5)
            first, _ = fixture.store.create_or_get(
                fixture.owners['alice'], 'ecommerce-v0',
                {'request_id': 'multi-instance-first-001', 'user_question': 'hold first slot'})
            try:
                peer.create_or_get(
                    fixture.owners['bobby'], 'ecommerce-v0',
                    {'request_id': 'multi-instance-second-001', 'user_question': 'must be rejected'})
            except ToolError as error:
                second = {'accepted': False, 'code': error.code}
            else:
                second = {'accepted': True, 'code': None}
            with fixture.store.transaction() as database:
                active = database.execute(
                    "SELECT count(*) FROM tasks WHERE status IN ('accepted','running')").fetchone()[0]
            report = {
                'status': 'passed' if second == {'accepted': False, 'code': 'BUSY'} and active == 1 else 'failed',
                'step': 'V4-S04b-global-slot', 'utc': datetime.now(timezone.utc).isoformat(),
                'first_task_id': first['id'], 'second': second, 'active_tasks': active,
                'independent_postgres_pools': 2, 'shared_redis_namespace': True,
                'qwen_calls': 0, 'qwen_ledger_sha256_before': before,
            }
            if report['status'] != 'passed':
                raise AssertionError('Cross-instance global slot was not enforced')
    except Exception as error:
        report.setdefault('status', 'failed')
        report['error_type'] = type(error).__name__
        raise
    finally:
        if peer is not None:
            peer.close()
        report['fixture_cleanup'] = fixture.cleanup
        report['qwen_ledger_sha256_after'] = file_hash(ledger)
        report['ledger_unchanged'] = report['qwen_ledger_sha256_after'] == before
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(output), **report}, indent=2))


if __name__ == '__main__':
    main()
