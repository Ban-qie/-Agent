"""Password/session boundary for the isolated multiuser ecommerce profile."""
import secrets
import sqlite3
import time

from flask import g, jsonify, request, session
from flask_session.base import ServerSideSessionInterface

from data_formulator.ecommerce.account_store import LoginLimited, LoginRejected
from data_formulator.ecommerce.contracts import StorageUnavailable


PREFIX = '/api/ecommerce/auth'


def install_password_auth(app, store, *, origin=None, http_profile=None, clock=time.time):
    from data_formulator.ecommerce.deployment import HttpProfile
    http_profile = http_profile or HttpProfile.local(origin or 'http://127.0.0.1:5567')
    origin = http_profile.origin
    if not isinstance(app.session_interface, ServerSideSessionInterface):
        raise ValueError('Server-side sessions are required')
    if not app.secret_key or len(app.secret_key) < 32:
        raise ValueError('A strong session secret is required')
    app.config.update(SESSION_COOKIE_NAME=('v4_session' if http_profile.name == 'production' else 'v3_session'),
                      SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=False,
                      PERMANENT_SESSION_LIFETIME=1800, MAX_CONTENT_LENGTH=8192)
    app.config['SESSION_COOKIE_SECURE'] = http_profile.secure_cookie
    app.extensions['v3_accounts'] = store

    def error(code, message, status):
        return jsonify(error={'code': code, 'message': message}), status

    def rotate():
        # regenerate() ignores an empty session, so regenerate before clearing.
        session['_rotate'] = True
        app.session_interface.regenerate(session)
        session.clear()
        session.permanent = True
        session['csrf'] = secrets.token_urlsafe(32)

    @app.before_request
    def authenticate():
        g.v3_principal = None
        if http_profile.name == 'local':
            if request.remote_addr not in ('127.0.0.1', '::1') or request.host != http_profile.host:
                return error('ACCESS_DENIED', 'Loopback development only', 403)
        elif request.scheme != 'https' or request.host.lower() != http_profile.host:
            return error('ACCESS_DENIED', 'HTTPS host rejected', 403)
        supplied_origin = request.headers.get('Origin')
        if supplied_origin and supplied_origin != origin:
            return error('ACCESS_DENIED', 'Origin rejected', 403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            supplied_csrf = request.headers.get('X-CSRF-Token', '')
            expected_csrf = session.get('csrf')
            if (supplied_origin != origin or not isinstance(expected_csrf, str)
                    or not secrets.compare_digest(supplied_csrf, expected_csrf)):
                return error('CSRF_REJECTED', 'Reload the session and retry', 403)
        owner, version, expires = (session.get(k) for k in ('owner', 'auth_version', 'expires'))
        if owner:
            try:
                if not isinstance(expires, (int, float)) or clock() >= expires:
                    raise LoginRejected()
                g.v3_principal = store.session_principal(session.sid, clock())
            except LoginRejected:
                rotate()
            except (sqlite3.Error, StorageUnavailable):
                return error('AUTH_UNAVAILABLE', 'Authentication is temporarily unavailable', 503)
        public = {('GET', PREFIX + '/status'), ('POST', PREFIX + '/login')}
        if request.path.startswith('/api/') and (request.method, request.path) not in public:
            if g.v3_principal is None:
                return error('AUTH_REQUIRED', 'Please log in', 401)

    @app.after_request
    def no_cache(response):
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get(PREFIX + '/status')
    def status():
        if not session.get('csrf'):
            rotate()
        result = {'authenticated': g.v3_principal is not None, 'csrf_token': session['csrf']}
        if g.v3_principal:
            result.update(user_id=g.v3_principal['id'], username=g.v3_principal['username'])
        return jsonify(result)

    @app.post(PREFIX + '/login')
    def login():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'username', 'password'}:
            return error('INVALID_REQUEST', 'Username and password are required', 400)
        try:
            principal = store.login(data['username'], data['password'], request.remote_addr)
        except LoginRejected:
            return error('LOGIN_REJECTED', 'Invalid login credentials', 401)
        except LoginLimited:
            return error('RATE_LIMIT', 'Try again later', 429)
        except (sqlite3.Error, StorageUnavailable):
            return error('AUTH_UNAVAILABLE', 'Authentication is temporarily unavailable', 503)
        rotate()
        try:
            store.open_session(session.sid, principal, clock() + 1800)
        except (sqlite3.Error, StorageUnavailable):
            return error('AUTH_UNAVAILABLE', 'Authentication is temporarily unavailable', 503)
        session.update(owner=principal['id'], auth_version=principal['auth_version'], expires=clock() + 1800)
        return jsonify(user_id=principal['id'], username=principal['username'], csrf_token=session['csrf'])

    @app.post(PREFIX + '/logout')
    def logout():
        try:
            store.close_session(session.sid)
        except (sqlite3.Error, StorageUnavailable):
            return error('AUTH_UNAVAILABLE', 'Authentication is temporarily unavailable', 503)
        rotate()
        return jsonify(logged_out=True, csrf_token=session['csrf'])
