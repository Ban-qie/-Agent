"""Full local 2 GiB gate using real PostgreSQL/Redis and stub model replies."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'py-src'))

from devtools.v4_offline_stress import current_rss_bytes, file_hash, percentile

ORIGIN = 'http://127.0.0.1:5567'
APP_CAP = 1024 * 1024 * 1024
POSTGRES_CAP = 256 * 1024 * 1024
REDIS_CAP = 96 * 1024 * 1024
PROXY_RESERVE = 64 * 1024 * 1024
TOTAL_CAP = APP_CAP + POSTGRES_CAP + REDIS_CAP + PROXY_RESERVE


def next_output():
    directory = ROOT / 'docs/verification/V4-S04'
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for path in directory.glob('full-offline-stress-attempt*.json'):
        try:
            attempts.append(int(path.stem.rsplit('attempt', 1)[1]))
        except (IndexError, ValueError):
            pass
    return directory / f'full-offline-stress-attempt{max(attempts, default=0) + 1}.json'


def login(client, username, password):
    status = client.get('/api/ecommerce/auth/status', base_url=ORIGIN)
    assert status.status_code == 200
    csrf = status.json['csrf_token']
    response = client.post('/api/ecommerce/auth/login', base_url=ORIGIN,
                           headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf},
                           json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_json()
    return response.json['csrf_token']


def wait_task(client, task_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get('/api/ecommerce/tasks/' + task_id, base_url=ORIGIN)
        assert response.status_code == 200
        if response.json['status'] in {'success', 'cancelled', 'failed', 'interrupted',
                                       'waiting_clarification', 'empty_result'}:
            return response.json
        time.sleep(0.02)
    raise AssertionError('task deadline exceeded')


def child(output: Path, commit: str):
    import psycopg2
    from psycopg2.extensions import make_dsn
    from dotenv import dotenv_values
    from data_formulator.ecommerce.multiuser_app import create_app
    from data_formulator.ecommerce.multiuser_routes import install_routes
    from data_formulator.ecommerce.multiuser_service import MultiuserService
    from data_formulator.ecommerce.postgres_store import PostgresTaskStore
    from data_formulator.ecommerce.redis_runtime import RedisCache, RedisClient, RedisCoordinator
    from data_formulator.ecommerce.task_service import TaskService
    from data_formulator.ecommerce.website_usage import WebsiteUsage

    started = time.time()
    config = dotenv_values(ROOT / 'deploy/ecommerce/.env.v4.private')
    schema = 'v4_stress_' + uuid.uuid4().hex[:12]
    namespace = 'ecommerce:v4:stress:' + uuid.uuid4().hex[:12] + ':'
    dsn = make_dsn(host='127.0.0.1', port=int(config['V4_POSTGRES_PORT']),
                   dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
                   password=config['V4_POSTGRES_PASSWORD'])
    redis = RedisClient('127.0.0.1', int(config['V4_REDIS_PORT']), config['V4_REDIS_PASSWORD'],
                        namespace=namespace)
    coordinator = RedisCoordinator(redis)
    admin = psycopg2.connect(dsn, connect_timeout=2)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute('CREATE SCHEMA "' + schema + '"')

    store = PostgresTaskStore(dsn, schema=schema, coordinator=coordinator, max_connections=5)
    store.initialize()
    users = [('alice', 'offline-A'), ('bobby', 'offline-B')] + [
        (f'user{number:02d}', f'offline-{number:02d}') for number in range(3, 11)]
    owners = {}
    for username, password in users:
        owners[username] = store.create(username, password)
        store.create_workspace(owners[username])

    release = threading.Event()
    entered = threading.Event()
    active_lock = threading.Lock()
    active = peak_active = model_stub_calls = 0

    class OfflineClient:
        def __init__(self):
            self.answers = [
                {'action': 'analyze', 'canonical_question': '\u5206\u67902018\u5e741\u6708\u9500\u552e\u989d', 'question': ''},
                {'decision': 'approve', 'question': ''},
                {'fact_ids': ['current.sales_amount', 'scope']},
            ]
            self.deadline = self.checkpoint = None

        def get_completion(self, messages, **kwargs):
            del messages, kwargs
            nonlocal active, peak_active, model_stub_calls
            model_stub_calls += 1
            if len(self.answers) == 3:
                with active_lock:
                    active += 1
                    peak_active = max(peak_active, active)
                    entered.set()
                try:
                    if not release.wait(10):
                        raise TimeoutError('offline release timeout')
                finally:
                    with active_lock:
                        active -= 1
            answer = self.answers.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))],
                usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))

    business = MultiuserService(store, output.parent / (output.stem + '-audit'), lambda *_args: OfflineClient())
    service = TaskService(store, business, WebsiteUsage(store), max_workers=1, max_slots=1)
    app = create_app(output.parent / (output.stem + '-runtime'), secret_key=secrets.token_hex(32),
                     origin=ORIGIN, store=store, session_cache=RedisCache(redis))
    install_routes(app, service)
    clients = [app.test_client() for _ in users]
    csrf = [login(client, *credentials) for client, credentials in zip(clients, users)]

    peak_rss = current_rss_bytes()
    stop_monitor = threading.Event()

    def monitor():
        nonlocal peak_rss
        while not stop_monitor.is_set():
            peak_rss = max(peak_rss, current_rss_bytes())
            stop_monitor.wait(0.02)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    read_latencies = []
    observed_read_statuses = []
    cleanup = {'schema': False, 'redis': False}
    passed = False
    result = {}
    try:
        body = {'request_id': 'full-stress-001', 'user_question': 'analyze January 2018 sales amount'}
        submit_started = time.perf_counter()
        first = clients[0].post('/api/ecommerce/analyze', base_url=ORIGIN,
                                headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf[0]}, json=body)
        submit_ms = (time.perf_counter() - submit_started) * 1000
        assert first.status_code == 202, first.get_json()
        task_id = first.json['task_id']
        assert entered.wait(5)

        duplicate = clients[0].post('/api/ecommerce/analyze', base_url=ORIGIN,
                                    headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf[0]}, json=body)
        assert duplicate.status_code == 202 and duplicate.json['task_id'] == task_id

        busy_started = time.perf_counter()
        busy = clients[1].post('/api/ecommerce/analyze', base_url=ORIGIN,
                               headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf[1]},
                               json={'request_id': 'full-stress-002', 'user_question': 'offline busy probe'})
        busy_ms = (time.perf_counter() - busy_started) * 1000
        assert busy.status_code == 429 and busy.json['error']['code'] == 'BUSY'

        def read_once(client):
            began = time.perf_counter()
            response = client.get('/api/ecommerce/workspace', base_url=ORIGIN)
            return response.status_code, time.perf_counter() - began

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(read_once, client) for _round in range(4) for client in clients]
            for future in as_completed(futures):
                status, latency = future.result()
                observed_read_statuses.append(status)
                assert status == 200
                read_latencies.append(latency)

        release.set()
        completion_started = time.perf_counter()
        completed = wait_task(clients[0], task_id)
        completion_seconds = time.perf_counter() - completion_started
        assert completed['status'] == 'success', completed

        owner = owners['alice']
        with store.transaction() as database:
            counts = {
                'tasks': database.execute('SELECT count(*) FROM tasks').fetchone()[0],
                'nodes': database.execute('SELECT count(*) FROM nodes').fetchone()[0],
                'usage': database.execute('SELECT count(*) FROM website_usage').fetchone()[0],
            }
            task_statuses = {
                row[0]: row[1] for row in database.execute(
                    'SELECT status,count(*) FROM tasks GROUP BY status').fetchall()
            }
            task_owner = database.execute('SELECT owner FROM tasks WHERE id=?', (task_id,)).fetchone()[0]
        assert counts == {'tasks': 1, 'nodes': 1, 'usage': 3}
        assert task_statuses == {'success': 1} and task_owner == owner
        passed = True
        result = {
            'status': 'passed', 'preflight_only': False,
            'utc': datetime.now(timezone.utc).isoformat(), 'git_commit': commit,
            'resource_budget_bytes': {'application': APP_CAP, 'postgres': POSTGRES_CAP,
                                      'redis': REDIS_CAP, 'proxy_reserve': PROXY_RESERVE,
                                      'bounded_total': TOTAL_CAP, 'target_total': 2 * 1024 * 1024 * 1024},
            'resource_cap_enforced_by_parent_job': True,
            'users': len(users), 'analysis_slots': 1, 'peak_active_analysis': peak_active,
            'application_parent_peak_rss_mib': round(peak_rss / (1024 * 1024), 3),
            'read_requests': len(read_latencies), 'read_p50_ms': percentile(read_latencies, .5),
            'read_p95_ms': percentile(read_latencies, .95), 'submit_ms': round(submit_ms, 3),
            'busy_rejection_ms': round(busy_ms, 3), 'busy_status': busy.status_code,
            'first_task_completion_poll_seconds': round(completion_seconds, 3),
            'postgres_counts': counts, 'task_statuses': task_statuses,
            'busy_request_persisted': False,
            'model_stub_calls': model_stub_calls, 'qwen_calls': 0,
            'unexpected_5xx': 0, 'oom_or_restart_observed': False,
            'source_sha256': {
                'devtools/v4_full_offline_stress.py': file_hash(Path(__file__)),
                'deploy/ecommerce/compose.yml': file_hash(ROOT / 'deploy/ecommerce/compose.yml'),
            },
        }
    except Exception as error:
        result = {
            'status': 'failed', 'stage': 'capacity_harness',
            'error_type': type(error).__name__, 'observed_read_statuses': observed_read_statuses,
            'application_parent_peak_rss_mib': round(peak_rss / (1024 * 1024), 3),
            'model_stub_calls': model_stub_calls, 'qwen_calls': 0,
        }
        raise
    finally:
        release.set()
        stop_monitor.set()
        monitor_thread.join(timeout=2)
        service.close()
        store.close()
        try:
            scan = redis.execute('SCAN', 0, 'MATCH', namespace + '*', 'COUNT', 1000)
            for key in (scan[1] if isinstance(scan, list) and len(scan) == 2 else []):
                redis.execute('DEL', key)
            cleanup['redis'] = True
        finally:
            if passed:
                with admin.cursor() as cursor:
                    cursor.execute('DROP SCHEMA "' + schema + '" CASCADE')
                cleanup['schema'] = True
            admin.close()
        result['cleanup'] = cleanup
        result['duration_seconds'] = round(time.time() - started, 3)
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', type=Path)
    parser.add_argument('--git-commit', default='')
    args = parser.parse_args()
    if args.child:
        child(args.child.resolve(), args.git_commit)
        return

    from data_formulator.ecommerce.process_limits import constrain_process
    output = next_output()
    ledger = ROOT / '.local/verification/qwen-usage.json'
    ledger_before = file_hash(ledger)
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                            text=True, check=False).stdout.strip()
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child', str(output),
                                '--git-commit', commit])
    close_job = constrain_process(process, memory_bytes=APP_CAP, active_processes=8)
    try:
        return_code = process.wait(timeout=180)
        job_peak = close_job.peak_memory_bytes()
    finally:
        close_job()
    if return_code or not output.is_file():
        if not output.is_file():
            output.write_text(json.dumps({'status': 'failed', 'exit_code': return_code,
                                          'qwen_calls': 0}, indent=2), encoding='utf-8')
        raise SystemExit(return_code or 1)
    result = json.loads(output.read_text(encoding='utf-8'))
    result['application_job_peak_mib'] = round(job_peak / (1024 * 1024), 3)
    result['qwen_ledger_sha256_before'] = ledger_before
    result['qwen_ledger_sha256_after'] = file_hash(ledger)
    if result['qwen_ledger_sha256_before'] != result['qwen_ledger_sha256_after']:
        result.update(status='failed', error='Qwen ledger changed')
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(output), **result}, indent=2))
    raise SystemExit(0 if result.get('status') == 'passed' else 1)


if __name__ == '__main__':
    main()
