import sqlite3

import pytest

from data_formulator.ecommerce.account_store import AccountStore, LoginLimited, LoginRejected


@pytest.fixture
def store(tmp_path):
    value = AccountStore(tmp_path / 'accounts.sqlite')
    value.initialize()
    return value


def test_real_password_and_stable_owner(store):
    owner = store.create('alice', 'valid-password-A')
    assert store.login('alice', 'valid-password-A', '127.0.0.1')['id'] == owner
    with pytest.raises(LoginRejected):
        store.login('alice', 'wrong-password-A', '127.0.0.1')
    with pytest.raises(LoginRejected):
        store.login('nobody', 'valid-password-A', '127.0.0.1')
    with sqlite3.connect(store.path) as db:
        hashed = db.execute('SELECT password_hash FROM accounts').fetchone()[0]
    assert hashed.startswith('scrypt:') and 'valid-password-A' not in hashed
    assert AccountStore(store.path).login('alice', 'valid-password-A', '127.0.0.1')['id'] == owner


def test_disable_and_password_change_revoke_old_principals(store):
    owner = store.create('alice', 'valid-password-A')
    first = store.login('alice', 'valid-password-A', 'source')
    store.change_password(owner, 'new-password-for-A')
    with pytest.raises(LoginRejected):
        store.principal(owner, first['auth_version'])
    with pytest.raises(LoginRejected):
        store.login('alice', 'valid-password-A', 'source')
    second = store.login('alice', 'new-password-for-A', 'source')
    assert second['id'] == owner
    store.revoke(owner, disable=True)
    with pytest.raises(LoginRejected):
        store.principal(owner, second['auth_version'])
    with pytest.raises(LoginRejected):
        store.login('alice', 'new-password-for-A', 'source')


def test_rate_limit_persists_and_other_account_has_opportunity(store):
    now = [100.0]
    store.clock = lambda: now[0]
    store.create('alice', 'valid-password-A')
    bob = store.create('bobby', 'valid-password-B')
    for _ in range(5):
        with pytest.raises(LoginRejected):
            store.login('alice', 'wrong-password-A', 'source')
    reopened = AccountStore(store.path, clock=store.clock)
    with pytest.raises(LoginLimited):
        reopened.login('alice', 'valid-password-A', 'source')
    assert reopened.login('bobby', 'valid-password-B', 'source')['id'] == bob
    now[0] += 60
    assert reopened.login('alice', 'valid-password-A', 'source')


@pytest.mark.parametrize('name,password', [('x', 'long-password'), ('../alice', 'long-password'),
                                         ('alice', 'short'), ('alice', '\ud800' * 12)])
def test_invalid_credentials_never_create_account(store, name, password):
    with pytest.raises(ValueError):
        store.create(name, password)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM accounts').fetchone()[0] == 0


def test_account_capacity_and_duplicates(store):
    store.create('alice', 'valid-password-A')
    with pytest.raises(sqlite3.IntegrityError):
        store.create('alice', 'valid-password-B')
    for index in range(9):
        store.create(f'user{index}', 'valid-password-A')
    with pytest.raises(ValueError, match='capacity'):
        store.create('extra', 'valid-password-A')


def test_malformed_login_username_rejected_then_valid_login(store):
    owner = store.create('alice', 'valid-password-A')
    with pytest.raises(LoginRejected):
        store.login('\ud800', 'valid-password-A', 'loopback')
    assert store.login('alice', 'valid-password-A', 'loopback')['id'] == owner
