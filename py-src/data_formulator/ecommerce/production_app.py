"""Fail-closed production factory for the invited-user ecommerce application."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re


ORIGIN = 'https://ecominsight.cn'
TRUSTED_PROXY = '172.29.0.10/32'
LEDGER_HASH = re.compile(r'^[a-f0-9]{64}$')


@dataclass(frozen=True, repr=False)
class ProductionSettings:
    postgres_host: str
    postgres_database: str
    postgres_user: str
    postgres_password: str
    postgres_schema: str
    redis_host: str
    redis_password: str
    session_secret: str
    legacy_ledger_sha256: str
    runtime_root: Path
    snapshot_root: Path

    @classmethod
    def from_environment(cls, environment=None):
        env = os.environ if environment is None else environment
        required = ('V4_POSTGRES_HOST', 'V4_POSTGRES_DB', 'V4_POSTGRES_USER',
                    'V4_POSTGRES_PASSWORD', 'V4_POSTGRES_SCHEMA', 'V4_REDIS_HOST',
                    'V4_REDIS_PASSWORD', 'V4_SESSION_SECRET', 'V4_LEGACY_LEDGER_SHA256',
                    'V4_RUNTIME_ROOT', 'V4_SNAPSHOT_ROOT', 'QWEN_API_KEY')
        if any(not isinstance(env.get(name), str) or not env[name] for name in required):
            raise ValueError('Required production configuration is unavailable')
        if (env.get('V4_ORIGIN') != ORIGIN or env.get('PYTHON_DOTENV_DISABLED') != '1'
                or env.get('ECOMMERCE_RESTRICTED', '').lower() != 'true'
                or env.get('QWEN_ENABLED', '').lower() != 'true'
                or env.get('QWEN_ENDPOINT') != 'openai'
                or env.get('QWEN_API_BASE') != 'https://dashscope.aliyuncs.com/compatible-mode/v1'
                or env.get('QWEN_MODELS') != 'qwen-flash'):
            raise ValueError('Production profile values differ from the frozen configuration')
        secret = env['V4_SESSION_SECRET']
        if not re.fullmatch(r'[a-fA-F0-9]{64,128}', secret):
            raise ValueError('Session secret must be 64-128 hexadecimal characters')
        if len(env['V4_POSTGRES_PASSWORD']) < 16 or len(env['V4_REDIS_PASSWORD']) < 16 or len(env['QWEN_API_KEY']) < 16:
            raise ValueError('Production credentials do not meet the minimum length')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', env['V4_POSTGRES_SCHEMA']):
            raise ValueError('Invalid production database schema')
        ledger = env['V4_LEGACY_LEDGER_SHA256'].lower()
        if not LEDGER_HASH.fullmatch(ledger):
            raise ValueError('Invalid legacy ledger fingerprint')
        root = Path(env['V4_RUNTIME_ROOT'])
        snapshot_root = Path(env['V4_SNAPSHOT_ROOT'])
        if not root.is_absolute() or not snapshot_root.is_absolute():
            raise ValueError('Production paths must be absolute')
        return cls(env['V4_POSTGRES_HOST'], env['V4_POSTGRES_DB'], env['V4_POSTGRES_USER'],
                   env['V4_POSTGRES_PASSWORD'], env['V4_POSTGRES_SCHEMA'], env['V4_REDIS_HOST'],
                   env['V4_REDIS_PASSWORD'], secret, ledger, root, snapshot_root)


def _snapshot_ready(catalog):
    try:
        if len(catalog) != 1:
            return False
        source = next(iter(catalog.values()))
        path = Path(source['path'])
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
    except (KeyError, OSError, TypeError, ValueError):
        return False


def create_production_app(settings=None, *, store=None, session_cache=None, coordinator=None,
                          catalog=None, client_factory=None):
    from flask import redirect, send_from_directory
    from psycopg2.extensions import make_dsn
    from data_formulator.ecommerce.executor import accepted_catalog
    from data_formulator.ecommerce.multiuser_app import create_app
    from data_formulator.ecommerce.multiuser_routes import install_routes
    from data_formulator.ecommerce.multiuser_service import MultiuserService
    from data_formulator.ecommerce.postgres_store import PostgresTaskStore
    from data_formulator.ecommerce.qwen_client import configured_qwen
    from data_formulator.ecommerce.redis_runtime import RedisCache, RedisClient, RedisCoordinator
    from data_formulator.ecommerce.task_service import TaskService
    from data_formulator.ecommerce.website_usage import WebsiteUsage

    settings = settings or ProductionSettings.from_environment()
    settings.runtime_root.mkdir(parents=True, exist_ok=True)
    redis = None
    if store is None:
        redis = RedisClient(settings.redis_host, 6379, settings.redis_password,
                            timeout=.5, namespace='ecommerce:v4:')
        coordinator = coordinator or RedisCoordinator(redis)
        dsn = make_dsn(host=settings.postgres_host, port=5432, dbname=settings.postgres_database,
                       user=settings.postgres_user, password=settings.postgres_password)
        store = PostgresTaskStore(dsn, schema=settings.postgres_schema,
                                  coordinator=coordinator, max_connections=5)
        session_cache = session_cache or RedisCache(redis)
    catalog = accepted_catalog(settings.snapshot_root) if catalog is None else catalog
    client_factory = configured_qwen if client_factory is None else client_factory

    def database_ready():
        with store.transaction() as database:
            return database.execute('SELECT count(*) FROM accounts WHERE enabled=1').fetchone()[0] > 0

    def ledger_ready():
        with store.transaction() as database:
            database.execute('SELECT count(*) FROM website_usage').fetchone()
            archived = database.execute('SELECT sha256,payload FROM legacy_debug_archive WHERE id=1').fetchone()
        return (archived is not None and archived['sha256'] == settings.legacy_ledger_sha256
                and hashlib.sha256(bytes(archived['payload'])).hexdigest() == settings.legacy_ledger_sha256)

    service = None
    try:
        # A production process never resumes work accepted by an earlier process.
        store.interrupt_inflight()
        usage = WebsiteUsage(store, initialize=False)
        app = create_app(settings.runtime_root, secret_key=settings.session_secret,
                         origin=ORIGIN, profile='production', trusted_proxy_cidrs=(TRUSTED_PROXY,),
                         trusted_proxy_hops=1,
                         readiness_checks={'database': database_ready,
                                           'snapshot': lambda: _snapshot_ready(catalog),
                                           'ledger': ledger_ready},
                         store=store, session_cache=session_cache)
        business = MultiuserService(store, settings.runtime_root / 'audit', client_factory)
        service = TaskService(store, business, usage, max_workers=1, max_slots=1)
        install_routes(app, service)
        dist = Path(__file__).resolve().parents[1] / 'dist'

        @app.get('/')
        def home():
            return redirect('/multiuser')

        @app.get('/multiuser')
        def page():
            return send_from_directory(dist, 'index.html')

        @app.get('/<path:name>')
        def asset(name):
            return send_from_directory(dist, name)

        app.extensions['v4_store'] = store
        return app
    except BaseException:
        if service is not None:
            service.close()
        if hasattr(store, 'close'):
            store.close()
        raise
