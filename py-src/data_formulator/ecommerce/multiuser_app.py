"""Isolated app factory. Never imports the upstream global app or enables models."""
from pathlib import Path
import sqlite3

from cachelib import FileSystemCache
from flask import Flask, jsonify
from flask_session import Session

from data_formulator.ecommerce.account_store import AccountStore
from data_formulator.ecommerce.password_auth import install_password_auth
from data_formulator.ecommerce.policy import install_profile


def create_app(root, *, secret_key, origin='http://127.0.0.1:5567'):
    root = Path(root).resolve()
    if len(secret_key) < 32:
        raise ValueError('Explicit strong secret required')
    if origin != 'http://127.0.0.1:5567':
        raise ValueError('External serving is not enabled')
    # No implicit DB bootstrap or first-user creation on requests/startup.
    database = root / 'multiuser.sqlite'
    if not database.is_file():
        raise ValueError('Provision the isolated account database first')
    store = AccountStore(database)
    with store.transaction() as db:
        if not db.execute('SELECT 1 FROM accounts WHERE enabled=1 LIMIT 1').fetchone():
            raise ValueError('At least one controlled account is required')
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secret_key, SESSION_TYPE='cachelib', SESSION_USE_SIGNER=True,
                      SESSION_CACHELIB=FileSystemCache(str(root / 'sessions'), threshold=500),
                      ECOMMERCE_PROFILE='multiuser', V3_ROOT=str(root))
    Session(app)
    install_password_auth(app, store, origin=origin)
    install_profile(app)

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(error={'code': 'RESOURCE_LIMIT', 'message': 'Request too large'}), 413

    @app.errorhandler(sqlite3.Error)
    def unavailable(_error):
        return jsonify(error={'code': 'STORAGE_UNAVAILABLE', 'message': 'Storage unavailable'}), 503

    return app
