"""Isolated app factory. Never imports the upstream global app or enables models."""
from pathlib import Path
import sqlite3

from cachelib import FileSystemCache
from flask import Flask, jsonify
from flask_session import Session
from werkzeug.middleware.proxy_fix import ProxyFix

from data_formulator.ecommerce.account_store import AccountStore
from data_formulator.ecommerce.deployment import HttpProfile, TrustedProxyMiddleware
from data_formulator.ecommerce.password_auth import install_password_auth
from data_formulator.ecommerce.contracts import StorageUnavailable
from data_formulator.ecommerce.policy import install_profile


class StorageFailureMiddleware:
    """Sanitize storage errors raised before Flask creates a request context."""
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        from data_formulator.ecommerce.redis_runtime import RedisUnavailable
        try:
            return self.application(environ, start_response)
        except (StorageUnavailable, RedisUnavailable):
            payload = b'{"error":{"code":"STORAGE_UNAVAILABLE","message":"Storage unavailable"}}\n'
            start_response('503 Service Unavailable', [
                ('Content-Type', 'application/json'),
                ('Content-Length', str(len(payload))),
                ('Cache-Control', 'no-store'),
            ])
            return [payload]


def create_app(root, *, secret_key, origin='http://127.0.0.1:5567', profile='local',
               trusted_proxy_cidrs=(), trusted_proxy_hops=0, readiness_checks=None,
               store=None, session_cache=None):
    root = Path(root).resolve()
    if len(secret_key) < 32:
        raise ValueError('Explicit strong secret required')
    if profile == 'local':
        http = HttpProfile.local(origin)
    elif profile == 'production':
        http = HttpProfile.production(origin, trusted_proxy_cidrs=trusted_proxy_cidrs,
                                      trusted_proxy_hops=trusted_proxy_hops)
        if (not isinstance(readiness_checks, dict)
                or set(readiness_checks) != {'database', 'snapshot', 'ledger'}
                or not all(callable(check) for check in readiness_checks.values())):
            raise ValueError('Production readiness checks are required')
    else:
        raise ValueError('Unknown HTTP deployment profile')
    # No implicit DB bootstrap or first-user creation on requests/startup.
    database = root / 'multiuser.sqlite'
    if store is None:
        if not database.is_file():
            raise ValueError('Provision the isolated account database first')
        store = AccountStore(database)
    with store.transaction() as db:
        if not db.execute('SELECT 1 FROM accounts WHERE enabled=1 LIMIT 1').fetchone():
            raise ValueError('At least one controlled account is required')
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secret_key, SESSION_TYPE='cachelib', SESSION_USE_SIGNER=True,
                      SESSION_CACHELIB=session_cache or FileSystemCache(str(root / 'sessions'), threshold=500),
                      ECOMMERCE_PROFILE='multiuser', V3_ROOT=str(root),
                      ECOMMERCE_HTTP_PROFILE=http)
    Session(app)
    install_password_auth(app, store, http_profile=http)
    install_profile(app)
    if http.name == 'production':
        fixed = ProxyFix(app.wsgi_app, x_for=http.trusted_proxy_hops,
                         x_proto=http.trusted_proxy_hops, x_host=http.trusted_proxy_hops)
        app.wsgi_app = TrustedProxyMiddleware(fixed, http)

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/readyz')
    def readiness():
        checks = {'config': True}
        for name, check in (readiness_checks or {}).items():
            try:
                checks[name] = check() is True
            except Exception:
                checks[name] = False
        if not all(checks.values()):
            return jsonify(status='unavailable', checks=checks), 503
        return jsonify(status='ready', checks=checks)

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(error={'code': 'RESOURCE_LIMIT', 'message': 'Request too large'}), 413

    @app.errorhandler(sqlite3.Error)
    def unavailable(_error):
        return jsonify(error={'code': 'STORAGE_UNAVAILABLE', 'message': 'Storage unavailable'}), 503

    app.register_error_handler(StorageUnavailable, unavailable)
    from data_formulator.ecommerce.redis_runtime import RedisUnavailable
    app.register_error_handler(RedisUnavailable, unavailable)

    app.wsgi_app = StorageFailureMiddleware(app.wsgi_app)

    return app
