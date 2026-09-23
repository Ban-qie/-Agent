"""Isolated PostgreSQL/Redis fixture for V4 service fault injection."""
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'py-src'))

ORIGIN = 'http://127.0.0.1:5567'
TERMINAL = frozenset({'success', 'cancelled', 'failed', 'interrupted',
                      'waiting_clarification', 'empty_result'})


def file_hash(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def next_output():
    directory = ROOT / 'docs/verification/V4-S02'
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for path in directory.glob('fixture-attempt*.json'):
        try:
            attempts.append(int(path.stem.rsplit('attempt', 1)[1]))
        except (IndexError, ValueError):
            pass
    return directory / f'fixture-attempt{max(attempts, default=0) + 1}.json'


def next_step_output(prefix):
    directory = ROOT / 'docs/verification/V4-S02'
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for path in directory.glob(prefix + '-attempt*.json'):
        try:
            attempts.append(int(path.stem.rsplit('attempt', 1)[1]))
        except (IndexError, ValueError):
            pass
    return directory / f'{prefix}-attempt{max(attempts, default=0) + 1}.json'


@dataclass(frozen=True)
class FixtureIdentity:
    token: str
    schema: str
    redis_namespace: str
    runtime_root: Path
    audit_root: Path

    @classmethod
    def create(cls, base_root):
        token = uuid.uuid4().hex[:12]
        root = Path(base_root).resolve() / token
        return cls(token, 'v4_resilience_' + token,
                   'ecommerce:v4:resilience:' + token + ':',
                   root / 'runtime', root / 'audit')

    def validate(self, base_root):
        base = Path(base_root).resolve()
        if (not self.runtime_root.is_relative_to(base)
                or not self.audit_root.is_relative_to(base)
                or not self.schema.startswith('v4_resilience_')
                or not self.redis_namespace.startswith('ecommerce:v4:resilience:')
                or self.token not in self.schema
                or self.token not in self.redis_namespace):
            raise ValueError('Resilience fixture identity is not isolated')


class StubModelPlan:
    """Deterministic model double; it never constructs a network client."""
    def __init__(self, gate=None):
        self.calls = 0
        self.gate = gate
        self.entered = threading.Event()

    def client(self, *_args):
        plan = self

        class Client:
            deadline = None
            checkpoint = None

            def __init__(self):
                self.answers = [
                    {'action': 'analyze', 'canonical_question': '分析2018年1月销售额', 'question': ''},
                    {'decision': 'approve', 'question': ''},
                    {'fact_ids': ['current.sales_amount', 'scope']},
                ]

            def get_completion(self, messages, **kwargs):
                del messages, kwargs
                plan.calls += 1
                if plan.calls == 1 and plan.gate is not None:
                    plan.entered.set()
                    if not plan.gate.wait(10):
                        raise TimeoutError('Fixture model gate timed out')
                answer = self.answers.pop(0)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))],
                    usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))

        return Client()


class ScenarioModelPlan(StubModelPlan):
    def __init__(self, modes):
        super().__init__()
        self.modes = list(modes)

    def client(self, *_args):
        from data_formulator.ecommerce.contracts import ToolError

        mode = self.modes.pop(0) if self.modes else 'success'
        client = super().client()
        original = client.get_completion

        def completion(messages, **kwargs):
            if mode == 'slow':
                time.sleep(.2)
                raise ToolError('ANALYSIS_TIMEOUT', 'Task deadline exceeded')
            if mode == 'refused':
                raise ConnectionRefusedError('synthetic provider refusal')
            if mode == 'bad_response':
                self.calls += 1
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content='{invalid'))],
                    usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))
            return original(messages, **kwargs)

        client.get_completion = completion
        return client


class ResilienceFixture(AbstractContextManager):
    """Owns one disposable schema, Redis namespace, app, and stub model."""
    def __init__(self, *, base_root=None, keep_on_failure=True, model=None, origin=ORIGIN):
        from dotenv import dotenv_values

        self.base_root = Path(base_root or ROOT / '.local/v4-resilience').resolve()
        self.identity = FixtureIdentity.create(self.base_root)
        self.identity.validate(self.base_root)
        self.keep_on_failure = keep_on_failure
        self.origin = origin
        self.config = dotenv_values(ROOT / 'deploy/ecommerce/.env.v4.private')
        required = ('V4_POSTGRES_DB', 'V4_POSTGRES_USER', 'V4_POSTGRES_PASSWORD',
                    'V4_POSTGRES_PORT', 'V4_REDIS_PASSWORD', 'V4_REDIS_PORT')
        if any(not self.config.get(key) for key in required):
            raise ValueError('Private local service configuration is incomplete')
        self.admin = self.store = self.redis = self.service = self.app = None
        self.model = model or StubModelPlan()
        self.cleanup = {'schema': False, 'redis': False}

    def __enter__(self):
        import psycopg2
        from psycopg2.extensions import make_dsn
        from data_formulator.ecommerce.multiuser_app import create_app
        from data_formulator.ecommerce.multiuser_routes import install_routes
        from data_formulator.ecommerce.multiuser_service import MultiuserService
        from data_formulator.ecommerce.postgres_store import PostgresTaskStore
        from data_formulator.ecommerce.redis_runtime import RedisCache, RedisClient, RedisCoordinator
        from data_formulator.ecommerce.task_service import TaskService
        from data_formulator.ecommerce.website_usage import WebsiteUsage

        self.identity.runtime_root.mkdir(parents=True, exist_ok=False)
        self.identity.audit_root.mkdir(parents=True, exist_ok=False)
        dsn = make_dsn(host='127.0.0.1', port=int(self.config['V4_POSTGRES_PORT']),
                       dbname=self.config['V4_POSTGRES_DB'], user=self.config['V4_POSTGRES_USER'],
                       password=self.config['V4_POSTGRES_PASSWORD'])
        self.redis = RedisClient('127.0.0.1', int(self.config['V4_REDIS_PORT']),
                                 self.config['V4_REDIS_PASSWORD'], timeout=.5,
                                 namespace=self.identity.redis_namespace)
        coordinator = RedisCoordinator(self.redis)
        self.admin = psycopg2.connect(dsn, connect_timeout=2)
        self.admin.autocommit = True
        with self.admin.cursor() as cursor:
            cursor.execute('CREATE SCHEMA "' + self.identity.schema + '"')
        self.store = PostgresTaskStore(dsn, schema=self.identity.schema,
                                       coordinator=coordinator, max_connections=5)
        self.store.initialize()
        usage = WebsiteUsage(self.store)
        self.owners = {
            'alice': self.store.create('alice', 'fixture-password-A'),
            'bobby': self.store.create('bobby', 'fixture-password-B'),
        }
        for owner in self.owners.values():
            self.store.create_workspace(owner)
        self.business = MultiuserService(self.store, self.identity.audit_root, self.model.client)
        self.service = TaskService(self.store, self.business, usage, max_workers=1, max_slots=1)
        self.session_secret = secrets.token_hex(32)
        self.app = create_app(self.identity.runtime_root, secret_key=self.session_secret,
                              origin=self.origin, store=self.store,
                              session_cache=RedisCache(self.redis))
        self.app.config.update(TESTING=True, QWEN_API_KEY='fixture-key-never-dispatched')
        install_routes(self.app, self.service)
        return self

    def _clear_redis_namespace(self):
        cursor = 0
        while True:
            result = self.redis.execute('SCAN', cursor, 'MATCH',
                                        self.identity.redis_namespace + '*', 'COUNT', 1000)
            if not isinstance(result, list) or len(result) != 2:
                raise RuntimeError('Unexpected Redis SCAN response')
            cursor = int(result[0])
            for key in result[1]:
                self.redis.execute('DEL', key)
            if cursor == 0:
                break
        self.cleanup['redis'] = True

    def close(self, *, passed):
        if self.service is not None:
            self.service.close()
        if self.store is not None:
            self.store.close()
        if self.redis is not None:
            self._clear_redis_namespace()
        if self.admin is not None:
            if passed or not self.keep_on_failure:
                with self.admin.cursor() as cursor:
                    cursor.execute('DROP SCHEMA "' + self.identity.schema + '" CASCADE')
                self.cleanup['schema'] = True
            self.admin.close()

    def __exit__(self, exc_type, _exc, _traceback):
        self.close(passed=exc_type is None)
        return False


def login(client, username, password, origin=ORIGIN):
    status = client.get('/api/ecommerce/auth/status', base_url=origin)
    csrf = status.get_json()['csrf_token']
    response = client.post('/api/ecommerce/auth/login', base_url=origin,
                           headers={'Origin': origin, 'X-CSRF-Token': csrf},
                           json={'username': username, 'password': password})
    if response.status_code != 200:
        raise AssertionError(response.get_json())
    return response.get_json()['csrf_token']


def wait_task(client, task_id, timeout=15, origin=ORIGIN):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get('/api/ecommerce/tasks/' + task_id, base_url=origin)
        if response.status_code != 200:
            raise AssertionError(response.get_json())
        if response.get_json()['status'] in TERMINAL:
            return response.get_json()
        time.sleep(.02)
    raise AssertionError('Fixture task did not reach a terminal state')


class HttpHarness(AbstractContextManager):
    """Real loopback HTTP server used to test worker availability."""
    def __init__(self, app, origin):
        from werkzeug.serving import make_server, WSGIRequestHandler

        class QuietHandler(WSGIRequestHandler):
            def log(self, _type, _message, *args):
                del args

        parsed = urlsplit(origin)
        self.server = make_server(parsed.hostname, parsed.port, app, threaded=True,
                                  request_handler=QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name='v4-resilience-http', daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        return False


def timed_request(session, method, url, **kwargs):
    began = time.perf_counter()
    response = session.request(method, url, timeout=5, **kwargs)
    return response, time.perf_counter() - began


def http_login(session, origin, username, password):
    status = session.get(origin + '/api/ecommerce/auth/status', timeout=5)
    status.raise_for_status()
    csrf = status.json()['csrf_token']
    response = session.post(origin + '/api/ecommerce/auth/login', timeout=5,
                            headers={'Origin': origin, 'X-CSRF-Token': csrf},
                            json={'username': username, 'password': password})
    response.raise_for_status()
    return response.json()['csrf_token']


def baseline(output):
    started = time.time()
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = file_hash(ledger)
    fixture = ResilienceFixture()
    result = {}
    try:
        with fixture:
            alice = fixture.app.test_client()
            csrf = login(alice, 'alice', 'fixture-password-A')
            submitted = alice.post('/api/ecommerce/analyze', base_url=ORIGIN,
                                   headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf},
                                   json={'request_id': 'resilience-baseline-001',
                                         'user_question': 'analyze January 2018 sales amount'})
            if submitted.status_code != 202:
                raise AssertionError(submitted.get_json())
            task = wait_task(alice, submitted.get_json()['task_id'])
            if task['status'] != 'success':
                raise AssertionError(task)
            with fixture.store.transaction() as database:
                counts = {
                    'accounts': database.execute('SELECT count(*) FROM accounts').fetchone()[0],
                    'tasks': database.execute('SELECT count(*) FROM tasks').fetchone()[0],
                    'nodes': database.execute('SELECT count(*) FROM nodes').fetchone()[0],
                    'usage': database.execute('SELECT count(*) FROM website_usage').fetchone()[0],
                }
            if counts != {'accounts': 2, 'tasks': 1, 'nodes': 1, 'usage': 3}:
                raise AssertionError(counts)
            result = {
                'status': 'passed', 'step': 'V4-S02a', 'mode': 'offline_stub',
                'utc': datetime.now(timezone.utc).isoformat(),
                'git_commit': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                             capture_output=True, text=True, check=True).stdout.strip(),
                'schema': fixture.identity.schema,
                'redis_namespace': fixture.identity.redis_namespace,
                'runtime_root': str(fixture.identity.runtime_root.relative_to(ROOT)),
                'audit_root': str(fixture.identity.audit_root.relative_to(ROOT)),
                'postgres_counts': counts, 'model_stub_calls': fixture.model.calls,
                'qwen_calls': 0,
                'qwen_ledger_sha256_before': before,
                'source_sha256': {
                    'devtools/service_resilience.py': file_hash(Path(__file__)),
                    'py-src/data_formulator/ecommerce/postgres_store.py':
                        file_hash(ROOT / 'py-src/data_formulator/ecommerce/postgres_store.py'),
                    'py-src/data_formulator/ecommerce/redis_runtime.py':
                        file_hash(ROOT / 'py-src/data_formulator/ecommerce/redis_runtime.py'),
                },
            }
    except Exception as error:
        result = {'status': 'failed', 'step': 'V4-S02a', 'error_type': type(error).__name__,
                  'schema': fixture.identity.schema, 'redis_namespace': fixture.identity.redis_namespace,
                  'qwen_calls': 0, 'qwen_ledger_sha256_before': before}
        raise
    finally:
        result['cleanup'] = fixture.cleanup
        result['qwen_ledger_sha256_after'] = file_hash(ledger)
        result['ledger_unchanged'] = result['qwen_ledger_sha256_after'] == before
        result['duration_seconds'] = round(time.time() - started, 3)
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    if not result['ledger_unchanged']:
        raise AssertionError('Qwen debug ledger changed')
    return result


def bounded(output):
    import requests
    from data_formulator.ecommerce.task_store import input_fingerprint
    from data_formulator.ecommerce.workspace_repository import pack

    thresholds = {
        'T_health_max_seconds': 1.0,
        'T_task_max_seconds': 2.0,
        'T_cancel_max_seconds': 2.0,
        'T_recovery_max_seconds': 2.0,
        'T_busy_max_seconds': 2.0,
    }
    started = time.time()
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = file_hash(ledger)
    gate = threading.Event()
    model = StubModelPlan(gate=gate)
    # The local security profile deliberately accepts only this frozen origin.
    origin = ORIGIN
    fixture = ResilienceFixture(model=model, origin=origin)
    result = {}
    try:
        with fixture:
            with HttpHarness(fixture.app, origin):
                alice, bob = requests.Session(), requests.Session()
                csrf_a = http_login(alice, origin, 'alice', 'fixture-password-A')
                csrf_b = http_login(bob, origin, 'bobby', 'fixture-password-B')
                first = alice.post(origin + '/api/ecommerce/analyze', timeout=5,
                                   headers={'Origin': origin, 'X-CSRF-Token': csrf_a},
                                   json={'request_id': 'bounded-running-001',
                                         'user_question': 'analyze January 2018 sales amount'})
                if first.status_code != 202 or not model.entered.wait(5):
                    raise AssertionError(first.text)
                task_id = first.json()['task_id']

                health, health_seconds = timed_request(alice, 'GET', origin + '/healthz')
                task_response, task_seconds = timed_request(
                    alice, 'GET', origin + '/api/ecommerce/tasks/' + task_id)
                busy, busy_seconds = timed_request(
                    bob, 'POST', origin + '/api/ecommerce/analyze',
                    headers={'Origin': origin, 'X-CSRF-Token': csrf_b},
                    json={'request_id': 'bounded-busy-001', 'user_question': 'busy probe'})
                cancel, cancel_seconds = timed_request(
                    alice, 'POST', origin + '/api/ecommerce/tasks/' + task_id + '/cancel',
                    headers={'Origin': origin, 'X-CSRF-Token': csrf_a})

                observations = {
                    'health': {'status': health.status_code, 'seconds': round(health_seconds, 6)},
                    'task': {'status': task_response.status_code, 'seconds': round(task_seconds, 6)},
                    'busy': {'status': busy.status_code, 'seconds': round(busy_seconds, 6),
                             'code': busy.json().get('error', {}).get('code')},
                    'cancel': {'status': cancel.status_code, 'seconds': round(cancel_seconds, 6),
                               'requested': cancel.json().get('cancel_requested')},
                }
                if (health.status_code != 200 or task_response.status_code != 200
                        or busy.status_code != 429 or observations['busy']['code'] != 'BUSY'
                        or cancel.status_code != 200 or not observations['cancel']['requested']
                        or health_seconds > thresholds['T_health_max_seconds']
                        or task_seconds > thresholds['T_task_max_seconds']
                        or busy_seconds > thresholds['T_busy_max_seconds']
                        or cancel_seconds > thresholds['T_cancel_max_seconds']):
                    raise AssertionError(observations)

                gate.set()
                deadline = time.monotonic() + 5
                cancelled = None
                while time.monotonic() < deadline:
                    cancelled = alice.get(origin + '/api/ecommerce/tasks/' + task_id, timeout=5).json()
                    if cancelled['status'] in TERMINAL:
                        break
                    time.sleep(.02)
                if cancelled is None or cancelled['status'] != 'cancelled':
                    raise AssertionError(cancelled)

                recovery_id = uuid.uuid4().hex
                recovery_body = {'request_id': 'bounded-expired-001',
                                 'user_question': 'expired task must not run'}
                now = fixture.store.clock()
                with fixture.store.transaction() as database:
                    database.execute(
                        'INSERT INTO tasks(id,owner,workspace,request_id,fingerprint,body,status,version,'
                        'lease_expiry,created,updated) VALUES(?,?,?,?,?,?,\'accepted\',0,?,?,?)',
                        (recovery_id, fixture.owners['alice'], 'ecommerce-v0',
                         recovery_body['request_id'], input_fingerprint(recovery_body), pack(recovery_body),
                         now - 1, now - 2, now - 2))
                recovery_started = time.perf_counter()
                recovered = fixture.store.recover_expired()
                recovery_seconds = time.perf_counter() - recovery_started
                recovered_task = fixture.store.get_authorized(
                    fixture.owners['alice'], 'ecommerce-v0', recovery_id)
                observations['recovery'] = {
                    'count': recovered, 'status': recovered_task['status'],
                    'seconds': round(recovery_seconds, 6),
                }
                if (recovered != 1 or recovered_task['status'] != 'interrupted'
                        or recovery_seconds > thresholds['T_recovery_max_seconds']):
                    raise AssertionError(observations['recovery'])

                followup = alice.post(origin + '/api/ecommerce/analyze', timeout=5,
                                      headers={'Origin': origin, 'X-CSRF-Token': csrf_a},
                                      json={'request_id': 'bounded-followup-001',
                                            'user_question': 'analyze January 2018 sales amount'})
                if followup.status_code != 202:
                    raise AssertionError(followup.text)
                followup_id = followup.json()['task_id']
                deadline = time.monotonic() + 15
                followup_task = None
                while time.monotonic() < deadline:
                    followup_task = alice.get(origin + '/api/ecommerce/tasks/' + followup_id,
                                              timeout=5).json()
                    if followup_task['status'] in TERMINAL:
                        break
                    time.sleep(.02)
                if followup_task is None or followup_task['status'] != 'success':
                    raise AssertionError(followup_task)

                with fixture.store.transaction() as database:
                    running = database.execute(
                        "SELECT count(*) FROM tasks WHERE status IN ('accepted','running')").fetchone()[0]
                    usage = database.execute('SELECT count(*) FROM website_usage').fetchone()[0]
                result = {
                    'status': 'passed', 'step': 'V4-S02b', 'mode': 'offline_stub_http',
                    'utc': datetime.now(timezone.utc).isoformat(),
                    'git_commit': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                                 capture_output=True, text=True, check=True).stdout.strip(),
                    'thresholds': thresholds, 'observations': observations,
                    'terminal_statuses': {'cancelled_task': cancelled['status'],
                                          'expired_task': recovered_task['status'],
                                          'followup_task': followup_task['status']},
                    'active_tasks_after': running, 'website_usage_rows': usage,
                    'model_stub_calls': model.calls, 'qwen_calls': 0,
                    'qwen_ledger_sha256_before': before,
                    'source_sha256': {
                        'devtools/service_resilience.py': file_hash(Path(__file__)),
                        'deploy/ecommerce/gunicorn.conf.py':
                            file_hash(ROOT / 'deploy/ecommerce/gunicorn.conf.py'),
                        'deploy/ecommerce/compose.yml': file_hash(ROOT / 'deploy/ecommerce/compose.yml'),
                    },
                }
    except Exception as error:
        gate.set()
        result = {'status': 'failed', 'step': 'V4-S02b', 'error_type': type(error).__name__,
                  'thresholds': thresholds, 'qwen_calls': 0,
                  'qwen_ledger_sha256_before': before}
        raise
    finally:
        result['cleanup'] = fixture.cleanup
        result['qwen_ledger_sha256_after'] = file_hash(ledger)
        result['ledger_unchanged'] = result['qwen_ledger_sha256_after'] == before
        result['duration_seconds'] = round(time.time() - started, 3)
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    if not result['ledger_unchanged']:
        raise AssertionError('Qwen debug ledger changed')
    return result


def faults(output):
    import psycopg2
    import requests

    started = time.time()
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before = file_hash(ledger)
    origin = ORIGIN
    model = ScenarioModelPlan(['slow', 'success', 'refused', 'success',
                               'bad_response', 'success'])
    fixture = ResilienceFixture(model=model, origin=origin)
    observations = []
    counter = 0
    result = {}

    def reset_rate_limits():
        with fixture.store.transaction() as database:
            database.execute('DELETE FROM submit_limits')

    def submit(session, csrf, label, *, expected_status='success'):
        nonlocal counter
        counter += 1
        reset_rate_limits()
        response = session.post(origin + '/api/ecommerce/analyze', timeout=5,
                                headers={'Origin': origin, 'X-CSRF-Token': csrf},
                                json={'request_id': f'fault-{counter:03d}-{label}',
                                      'user_question': 'analyze January 2018 sales amount'})
        if response.status_code != 202:
            raise AssertionError((label, response.status_code, response.text))
        task_id = response.json()['task_id']
        deadline = time.monotonic() + 15
        task = None
        while time.monotonic() < deadline:
            task_response = session.get(origin + '/api/ecommerce/tasks/' + task_id, timeout=5)
            if task_response.status_code != 200:
                raise AssertionError((label, task_response.status_code, task_response.text))
            task = task_response.json()
            if task['status'] in TERMINAL:
                break
            time.sleep(.02)
        if task is None or task['status'] != expected_status:
            raise AssertionError((label, task))
        return task_id, task

    def verify_recovery(session, csrf, label):
        began = time.perf_counter()
        task_id, task = submit(session, csrf, label + '-recovery')
        elapsed = time.perf_counter() - began
        if elapsed > 15:
            raise AssertionError((label, elapsed))
        return {'task_id': task_id, 'status': task['status'], 'seconds': round(elapsed, 6)}

    def expected_http_failure(session, csrf, label, inject, release):
        nonlocal counter
        counter += 1
        reset_rate_limits()
        inject()
        try:
            began = time.perf_counter()
            response = session.post(origin + '/api/ecommerce/analyze', timeout=5,
                                    headers={'Origin': origin, 'X-CSRF-Token': csrf},
                                    json={'request_id': f'fault-{counter:03d}-{label}',
                                          'user_question': 'storage fault probe'})
            elapsed = time.perf_counter() - began
        finally:
            release()
        if response.status_code != 503 or elapsed > 4:
            raise AssertionError((label, response.status_code, elapsed, response.text))
        health, health_seconds = timed_request(requests.Session(), 'GET', origin + '/healthz')
        if health.status_code != 200 or health_seconds > 1:
            raise AssertionError((label, health.status_code, health_seconds))
        return {'status': response.status_code, 'seconds': round(elapsed, 6),
                'health_seconds': round(health_seconds, 6)}

    try:
        with fixture:
            with HttpHarness(fixture.app, origin):
                alice = requests.Session()
                csrf = http_login(alice, origin, 'alice', 'fixture-password-A')

                for label in ('model-slow', 'model-refused', 'model-bad-response'):
                    task_id, task = submit(alice, csrf, label, expected_status='failed')
                    observations.append({'fault': label, 'task_id': task_id,
                                         'fault_status': task['status'],
                                         'recovery': verify_recovery(alice, csrf, label)})

                lock_connection = psycopg2.connect(fixture.store.dsn, connect_timeout=2)
                lock_cursor = lock_connection.cursor()
                observations.append({
                    'fault': 'postgres-lock',
                    'failure': expected_http_failure(
                        alice, csrf, 'postgres-lock',
                        lambda: lock_cursor.execute('SELECT pg_advisory_xact_lock(846302905)'),
                        lambda: (lock_connection.rollback(), lock_cursor.close(), lock_connection.close())),
                    'recovery': verify_recovery(alice, csrf, 'postgres-lock'),
                })

                acquired = []
                def exhaust_pool():
                    for _ in range(5):
                        if not fixture.store._pool_slots.acquire(timeout=1):
                            raise AssertionError('could not exhaust fixture connection pool')
                        acquired.append(True)
                def release_pool():
                    while acquired:
                        acquired.pop()
                        fixture.store._pool_slots.release()
                observations.append({
                    'fault': 'postgres-pool',
                    'failure': expected_http_failure(alice, csrf, 'postgres-pool', exhaust_pool, release_pool),
                    'recovery': verify_recovery(alice, csrf, 'postgres-pool'),
                })

                original_port = fixture.redis.port
                observations.append({
                    'fault': 'redis-unavailable',
                    'failure': expected_http_failure(
                        alice, csrf, 'redis-unavailable',
                        lambda: setattr(fixture.redis, 'port', 1),
                        lambda: setattr(fixture.redis, 'port', original_port)),
                    'recovery': verify_recovery(alice, csrf, 'redis-unavailable'),
                })

                listener = socket.socket()
                listener.bind(('127.0.0.1', 0))
                listener.listen(1)
                timeout_port = listener.getsockname()[1]
                def stall_redis():
                    fixture.redis.port = timeout_port
                    def accept_once():
                        connection, _ = listener.accept()
                        time.sleep(1)
                        connection.close()
                    threading.Thread(target=accept_once, daemon=True).start()
                def restore_redis():
                    fixture.redis.port = original_port
                    listener.close()
                observations.append({
                    'fault': 'redis-timeout',
                    'failure': expected_http_failure(alice, csrf, 'redis-timeout', stall_redis, restore_redis),
                    'recovery': verify_recovery(alice, csrf, 'redis-timeout'),
                })

                original_analyze = fixture.business.analyze
                for label, error in (
                    ('readonly-directory', PermissionError('synthetic readonly directory')),
                    ('enospc', OSError(28, 'synthetic no space left')),
                    ('worker-exit', SystemExit(7)),
                ):
                    def fail(*_args, injected=error, **_kwargs):
                        raise injected
                    fixture.business.analyze = fail
                    task_id, task = submit(alice, csrf, label, expected_status='failed')
                    fixture.business.analyze = original_analyze
                    observations.append({'fault': label, 'task_id': task_id,
                                         'fault_status': task['status'],
                                         'recovery': verify_recovery(alice, csrf, label)})

                with fixture.store.transaction() as database:
                    active = database.execute(
                        "SELECT count(*) FROM tasks WHERE status IN ('accepted','running')").fetchone()[0]
                    unsettled = database.execute(
                        'SELECT count(*) FROM website_usage WHERE settlement IS NULL').fetchone()[0]
                    task_count = database.execute('SELECT count(*) FROM tasks').fetchone()[0]
                if active != 0:
                    raise AssertionError(('active tasks remain', active))
                result = {
                    'status': 'passed', 'step': 'V4-S02c', 'mode': 'offline_fault_matrix',
                    'utc': datetime.now(timezone.utc).isoformat(),
                    'git_commit': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                                 capture_output=True, text=True, check=True).stdout.strip(),
                    'faults': observations, 'fault_count': len(observations),
                    'task_count': task_count, 'active_tasks_after': active,
                    'unsettled_usage_after': unsettled,
                    'model_stub_calls': model.calls, 'qwen_calls': 0,
                    'unexpected_500': 0, 'qwen_ledger_sha256_before': before,
                }
    except Exception as error:
        result = {'status': 'failed', 'step': 'V4-S02c', 'error_type': type(error).__name__,
                  'completed_faults': observations, 'qwen_calls': 0,
                  'qwen_ledger_sha256_before': before}
        raise
    finally:
        result['cleanup'] = fixture.cleanup
        result['qwen_ledger_sha256_after'] = file_hash(ledger)
        result['ledger_unchanged'] = result['qwen_ledger_sha256_after'] == before
        result['duration_seconds'] = round(time.time() - started, 3)
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--step', choices=('baseline', 'bounded', 'faults'), default='baseline')
    args = parser.parse_args()
    output = args.output.resolve() if args.output else (
        next_output() if args.step == 'baseline' else next_step_output(args.step))
    if output.exists():
        raise SystemExit('Refusing to overwrite existing evidence')
    action = {'baseline': baseline, 'bounded': bounded, 'faults': faults}[args.step]
    result = action(output)
    print(json.dumps({'evidence': str(output), **result}, indent=2))


if __name__ == '__main__':
    main()
