import hashlib
from pathlib import Path
import secrets

from cachelib import FileSystemCache
import pytest

from data_formulator.ecommerce.production_app import ProductionSettings, create_production_app
from data_formulator.ecommerce.task_store import TaskStore
from data_formulator.ecommerce.website_usage import WebsiteUsage


LEDGER_HASH = '0fcdda9608e24c7397414f4bd493f5bde13b6b1b173414f7984214d0482fa0fc'
PROXY_HEADERS = {'X-Forwarded-For': '198.51.100.7', 'X-Forwarded-Host': 'ecominsight.cn',
                 'X-Forwarded-Proto': 'https'}


def environment(tmp_path):
    return {
        'V4_POSTGRES_HOST': 'postgres', 'V4_POSTGRES_DB': 'agent_ecommerce',
        'V4_POSTGRES_USER': 'agent_ecommerce', 'V4_POSTGRES_PASSWORD': 'p' * 20,
        'V4_POSTGRES_SCHEMA': 'ecommerce', 'V4_REDIS_HOST': 'redis',
        'V4_REDIS_PASSWORD': 'r' * 20, 'V4_SESSION_SECRET': secrets.token_hex(32),
        'V4_LEGACY_LEDGER_SHA256': LEDGER_HASH, 'V4_RUNTIME_ROOT': str(tmp_path / 'runtime'),
        'V4_SNAPSHOT_ROOT': str(tmp_path / 'snapshot'),
        'V4_ORIGIN': 'https://ecominsight.cn', 'PYTHON_DOTENV_DISABLED': '1',
        'ECOMMERCE_RESTRICTED': 'true', 'QWEN_ENABLED': 'true', 'QWEN_ENDPOINT': 'openai',
        'QWEN_API_KEY': 'q' * 20, 'QWEN_API_BASE': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'QWEN_MODELS': 'qwen-flash',
    }


@pytest.mark.parametrize(('key', 'value'), [
    ('V4_ORIGIN', 'http://ecominsight.cn'),
    ('V4_SESSION_SECRET', 'weak'),
    ('V4_POSTGRES_SCHEMA', 'public;drop'),
    ('QWEN_ENABLED', 'false'),
    ('QWEN_API_BASE', 'https://attacker.invalid'),
])
def test_production_settings_reject_drift(tmp_path, key, value):
    values = environment(tmp_path)
    values[key] = value
    with pytest.raises(ValueError):
        ProductionSettings.from_environment(values)


@pytest.mark.parametrize('missing', [
    'V4_POSTGRES_PASSWORD', 'V4_REDIS_PASSWORD', 'V4_SESSION_SECRET', 'QWEN_API_KEY',
])
def test_production_settings_require_all_secrets(tmp_path, missing):
    values = environment(tmp_path)
    del values[missing]
    with pytest.raises(ValueError, match='configuration is unavailable'):
        ProductionSettings.from_environment(values)


def test_production_startup_interrupts_old_work_without_model_call(tmp_path):
    settings = ProductionSettings.from_environment(environment(tmp_path))
    store = TaskStore(tmp_path / 'source.sqlite')
    store.initialize()
    owner = store.create('alice', 'test-password-A')
    store.create_workspace(owner)
    task, _ = store.create_or_get(owner, 'ecommerce-v0', {
        'request_id': 'startup-task-001', 'user_question': 'offline startup test'})
    WebsiteUsage(store)
    ledger = b'preserved-debug-ledger'
    settings = ProductionSettings(
        settings.postgres_host, settings.postgres_database, settings.postgres_user,
        settings.postgres_password, settings.postgres_schema, settings.redis_host,
        settings.redis_password, settings.session_secret, hashlib.sha256(ledger).hexdigest(),
        settings.runtime_root, settings.snapshot_root)
    with store.transaction() as database:
        database.execute('CREATE TABLE legacy_debug_archive '
                         '(id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL,payload BLOB NOT NULL)')
        database.execute('INSERT INTO legacy_debug_archive VALUES(1,?,?)',
                         (settings.legacy_ledger_sha256, ledger))
    snapshot = tmp_path / 'orders.parquet'
    snapshot.write_bytes(b'offline-snapshot')
    catalog = {'snapshot': {'path': str(snapshot),
                            'sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest()}}
    calls = []
    app = create_production_app(
        settings, store=store, session_cache=FileSystemCache(str(tmp_path / 'sessions')),
        catalog=catalog, client_factory=lambda *args: calls.append(args))
    app.config['TESTING'] = True

    assert store.get_authorized(owner, 'ecommerce-v0', task['id'])['status'] == 'interrupted'
    client = app.test_client()
    options = dict(base_url='http://app:5567', headers=PROXY_HEADERS,
                   environ_overrides={'REMOTE_ADDR': '172.29.0.10'})
    assert client.get('/healthz', **options).json == {'status': 'ok'}
    ready = client.get('/readyz', **options)
    assert ready.status_code == 200 and all(ready.json['checks'].values())
    assert calls == []

    with store.transaction() as database:
        database.execute("UPDATE legacy_debug_archive SET sha256='" + '0' * 64 + "' WHERE id=1")
    unavailable = client.get('/readyz', **options)
    assert unavailable.status_code == 503
    assert unavailable.json['checks']['ledger'] is False
    assert calls == []
