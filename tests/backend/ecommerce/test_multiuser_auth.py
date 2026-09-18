import secrets

import pytest
from cachelib import FileSystemCache
from flask import Flask, g, jsonify
from flask_session import Session

from data_formulator.ecommerce.account_store import AccountStore
from data_formulator.ecommerce.password_auth import install_password_auth, PREFIX

ORIGIN = 'http://127.0.0.1:5567'


@pytest.fixture
def env(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY=secrets.token_hex(32), SESSION_TYPE='cachelib',
                      SESSION_USE_SIGNER=True,
                      SESSION_CACHELIB=FileSystemCache(str(tmp_path / 'sessions')))
    Session(app)
    store = AccountStore(tmp_path / 'accounts.sqlite')
    store.initialize()
    a = store.create('alice', 'test-password-A')
    b = store.create('bobby', 'test-password-B')
    clock = [100.0]
    store.clock = lambda: clock[0]
    install_password_auth(app, store, origin=ORIGIN, clock=lambda: clock[0])
    @app.get('/api/private')
    def private():
        return jsonify(owner=g.v3_principal['id'])
    return app, store, clock, a, b


def get(client, path, **kwargs):
    return client.get(path, base_url=ORIGIN, **kwargs)


def login(client, name='alice', password='test-password-A'):
    csrf = get(client, PREFIX + '/status').json['csrf_token']
    return client.post(PREFIX + '/login', base_url=ORIGIN,
                       headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf},
                       json={'username': name, 'password': password})


def test_login_identity_and_header_spoof(env):
    app, _, _, a, b = env
    client = app.test_client()
    assert get(client, '/api/private', headers={'X-Identity-Id': 'user:' + b}).status_code == 401
    assert login(client).json['user_id'] == a
    assert get(client, '/api/private', headers={'X-Identity-Id': 'user:' + b}).json == {'owner': a}
    assert get(client, '/api/private').headers['Cache-Control'] == 'no-store'


def test_wrong_password_unknown_account_then_valid(env):
    client = env[0].test_client()
    wrong = login(client, password='wrong-password-A')
    unknown = login(client, name='nobody')
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json == unknown.json
    assert login(client).status_code == 200


def test_csrf_origin_and_login_rotation(env):
    client = env[0].test_client()
    csrf = get(client, PREFIX + '/status').json['csrf_token']
    before = client.get_cookie('v3_session', domain='127.0.0.1').value
    for headers in ({}, {'Origin': 'https://attacker.invalid', 'X-CSRF-Token': csrf}):
        assert client.post(PREFIX + '/login', base_url=ORIGIN, headers=headers,
                           json={'username': 'alice', 'password': 'test-password-A'}).status_code == 403
    response = login(client)
    after = client.get_cookie('v3_session', domain='127.0.0.1').value
    assert before != after
    cookie = response.headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    replay = env[0].test_client()
    replay.set_cookie('v3_session', before, domain='127.0.0.1')
    assert get(replay, '/api/private').status_code == 401


def test_logout_expiry_disable_and_forged_cookie(env):
    app, store, clock, a, _ = env
    client = app.test_client()
    csrf = login(client).json['csrf_token']
    old = client.get_cookie('v3_session', domain='127.0.0.1').value
    assert client.post(PREFIX + '/logout', base_url=ORIGIN,
                       headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf}).status_code == 200
    client.set_cookie('v3_session', old, domain='127.0.0.1')
    assert get(client, '/api/private').status_code == 401
    assert login(client).status_code == 200
    clock[0] += 1800
    assert get(client, '/api/private').status_code == 401
    assert login(client).status_code == 200
    store.revoke(a, disable=True)
    assert get(client, '/api/private').status_code == 401
    client.set_cookie('v3_session', 'forged-cookie', domain='127.0.0.1')
    assert get(client, '/api/private').status_code == 401


def test_unavailable_store_fails_closed_and_nonlocal_blocked(env):
    app, store, _, _, _ = env
    client = app.test_client()
    assert login(client).status_code == 200
    store.path = store.path.parent / 'missing' / 'db.sqlite'
    assert get(client, '/api/private').status_code == 503
    assert get(client, PREFIX + '/status', environ_overrides={'REMOTE_ADDR': '203.0.113.1'}).status_code == 403
    assert client.get(PREFIX + '/status', base_url='http://attacker.invalid').status_code == 403


def test_late_request_cannot_restore_logged_out_session(env):
    app = env[0]
    client = app.test_client()
    csrf = login(client).json['csrf_token']
    old = client.get_cookie('v3_session', domain='127.0.0.1').value
    # Snapshot a request that started before logout, then saves after logout.
    with app.test_request_context('/api/private', base_url=ORIGIN,
                                  headers={'Cookie': 'v3_session=' + old}):
        from flask import session
        late_session = session._get_current_object()
    client.post(PREFIX + '/logout', base_url=ORIGIN,
                headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf})
    with app.test_request_context('/api/private', base_url=ORIGIN):
        late_session['late'] = True
        app.session_interface.save_session(app, late_session, app.response_class())
    client.set_cookie('v3_session', old, domain='127.0.0.1')
    assert get(client, '/api/private').status_code == 401


def test_independent_users_and_new_login_revokes_only_same_account(env):
    app, _, _, a, b = env
    alice, bob, again = (app.test_client() for _ in range(3))
    assert login(alice).status_code == 200
    assert login(bob, 'bobby', 'test-password-B').status_code == 200
    assert get(alice, '/api/private').json['owner'] == a
    assert get(bob, '/api/private').json['owner'] == b
    assert login(again).status_code == 200
    assert get(alice, '/api/private').status_code == 401
    assert get(bob, '/api/private').json['owner'] == b


def test_isolated_profile_closes_analysis_and_invalid_startup(tmp_path):
    from data_formulator.ecommerce.multiuser_app import create_app
    key = secrets.token_hex(32)
    with pytest.raises(ValueError):
        create_app(tmp_path, secret_key=key)
    store = AccountStore(tmp_path / 'multiuser.sqlite')
    store.initialize()
    store.create('alice', 'test-password-A')
    with pytest.raises(ValueError):
        create_app(tmp_path, secret_key='weak')
    with pytest.raises(ValueError):
        create_app(tmp_path, secret_key=key, origin='http://0.0.0.0:5567')
    client = create_app(tmp_path, secret_key=key).test_client()
    response = login(client)
    assert response.status_code == 200
    for path in ['/api/auth/tokens/save', '/api/ecommerce/analyze', '/api/ecommerce/query', '/api/unknown']:
        assert client.post(path, base_url=ORIGIN, json={},
                           headers={'Origin': ORIGIN, 'X-CSRF-Token': response.json['csrf_token']}).status_code == 403


def test_http_rate_limit_body_owner_and_csrf_on_logout(env):
    client = env[0].test_client()
    for _ in range(5):
        assert login(client, password='wrong-password-A').status_code == 401
    assert login(client).status_code == 429
    assert login(client, 'bobby', 'test-password-B').status_code == 200
    assert client.post(PREFIX + '/logout', base_url=ORIGIN).status_code == 403
    csrf = get(client, PREFIX + '/status').json['csrf_token']
    assert client.post(PREFIX + '/login', base_url=ORIGIN,
                       headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf},
                       json={'username': 'alice', 'password': 'test-password-A', 'owner': 'bobby'}).status_code == 400
    assert client.post(PREFIX + '/login', base_url=ORIGIN,
                       headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf},
                       data=b'x' * 8193, content_type='application/json').status_code == 413
