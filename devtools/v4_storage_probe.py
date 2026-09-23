"""Probe PostgreSQL durability and Redis sharing without model calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'py-src'))


def next_output():
    directory = ROOT / 'docs/verification/V4-S01'
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for path in directory.glob('storage-probe-attempt*.json'):
        try:
            attempts.append(int(path.stem.rsplit('attempt', 1)[1]))
        except (IndexError, ValueError):
            pass
    return directory / f'storage-probe-attempt{max(attempts, default=0) + 1}.json'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    import psycopg2
    from psycopg2.extensions import make_dsn
    from dotenv import dotenv_values
    from data_formulator.ecommerce.authorization import Principal
    from data_formulator.ecommerce.multiuser_app import create_app
    from data_formulator.ecommerce.postgres_store import PostgresTaskStore
    from data_formulator.ecommerce.redis_runtime import RedisCache, RedisClient, RedisCoordinator
    from data_formulator.ecommerce.task_store import input_fingerprint
    from data_formulator.ecommerce.website_usage import WebsiteUsage

    output = next_output()
    config = dotenv_values(ROOT / 'deploy/ecommerce/.env.v4.private')
    schema = 'v4_probe_' + uuid.uuid4().hex[:12]
    namespace = 'ecommerce:v4:probe:' + uuid.uuid4().hex[:12] + ':'
    dsn = make_dsn(host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
                   dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
                   password=config['V4_POSTGRES_PASSWORD'])
    redis = RedisClient('127.0.0.1', int(config['V4_REDIS_PORT']), config['V4_REDIS_PASSWORD'],
                        namespace=namespace)
    coordinator = RedisCoordinator(redis)
    admin = psycopg2.connect(dsn, connect_timeout=2)
    admin.autocommit = True
    store_a = store_b = None
    passed = False
    payload = {
        'status': 'failed', 'utc': datetime.now(timezone.utc).isoformat(),
        'schema': schema, 'redis_namespace_hash': hashlib.sha256(namespace.encode()).hexdigest(),
        'qwen_calls': 0, 'checks': {},
    }
    try:
        with admin.cursor() as cursor:
            cursor.execute('CREATE SCHEMA "' + schema + '"')
        store_a = PostgresTaskStore(dsn, schema=schema, coordinator=coordinator, max_connections=5)
        store_b = PostgresTaskStore(dsn, schema=schema, coordinator=coordinator, max_connections=5)
        store_a.initialize()
        usage = WebsiteUsage(store_a)
        alice = store_a.create('alice', 'offline-A')
        bob = store_a.create('bobby', 'offline-B')
        store_a.create_workspace(alice)
        store_a.create_workspace(bob)

        secret = secrets.token_hex(32)
        cache_a, cache_b = RedisCache(redis), RedisCache(redis)
        app_a = create_app(ROOT / '.local/v4-probe-a', secret_key=secret, store=store_a, session_cache=cache_a)
        app_b = create_app(ROOT / '.local/v4-probe-b', secret_key=secret, store=store_b, session_cache=cache_b)
        client_a, client_b = app_a.test_client(), app_b.test_client()
        origin = 'http://127.0.0.1:5567'
        status = client_a.get('/api/ecommerce/auth/status', base_url=origin)
        login = client_a.post('/api/ecommerce/auth/login', base_url=origin,
                              headers={'Origin': origin, 'X-CSRF-Token': status.json['csrf_token']},
                              json={'username': 'alice', 'password': 'offline-A'})
        assert login.status_code == 200
        cookie = client_a.get_cookie('v3_session', domain='127.0.0.1')
        client_b.set_cookie('v3_session', cookie.value, domain='127.0.0.1')
        shared = client_b.get('/api/ecommerce/auth/status', base_url=origin)
        assert shared.status_code == 200 and shared.json['authenticated'] and shared.json['user_id'] == alice
        payload['checks']['cross_instance_session'] = True

        body = {'request_id': 'storage-probe-001', 'user_question': 'offline storage probe'}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda store: store.create_or_get(alice, 'ecommerce-v0', body),
                                    (store_a, store_b)))
        assert results[0][0]['id'] == results[1][0]['id']
        assert sorted(result[1] for result in results) == [False, True]
        task = store_a.claim(results[0][0]['id'])
        reservation = usage.reserve(task, 1)
        usage.finish(reservation, {'input_tokens': 0, 'output_tokens': 0})
        final = store_b.finish_if_owner_version(task, {'state': 'success', 'result': {'probe': True}})
        assert final['status'] == 'success'
        replay, created = store_a.create_or_get(alice, 'ecommerce-v0', body)
        assert not created and replay['id'] == task['id']
        assert replay['fingerprint'] == input_fingerprint(body)
        payload['checks'].update(cross_instance_idempotency=True, task_fencing=True,
                                 website_usage_reservation=True)

        assert store_b.get_authorized(alice, 'ecommerce-v0', task['id'])['status'] == 'success'
        try:
            store_b.get_authorized(bob, 'ecommerce-v0', task['id'])
        except Exception as error:
            assert getattr(error, 'code', None) == 'NOT_FOUND'
        else:
            raise AssertionError('cross-user task read succeeded')
        payload['checks']['object_authorization'] = True
        payload.update(status='passed', postgres_tables=10, qwen_ledger_sha256=sha256(
            ROOT / '.local/verification/qwen-usage.json'))
        passed = True
    except Exception as error:
        payload['error_type'] = type(error).__name__
        raise
    finally:
        payload['temporary_schema_removed'] = False
        try:
            scan = redis.execute('SCAN', 0, 'MATCH', namespace + '*', 'COUNT', 100)
            for key in (scan[1] if isinstance(scan, list) and len(scan) == 2 else []):
                redis.execute('DEL', key)
        except Exception:
            payload['redis_cleanup'] = 'failed'
        else:
            payload['redis_cleanup'] = 'passed'
        if store_a:
            store_a.close()
        if store_b:
            store_b.close()
        if passed:
            with admin.cursor() as cursor:
                cursor.execute('DROP SCHEMA "' + schema + '" CASCADE')
            payload['temporary_schema_removed'] = True
        admin.close()
        output.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        print(json.dumps({'evidence': str(output), **payload}, indent=2))


if __name__ == '__main__':
    main()
