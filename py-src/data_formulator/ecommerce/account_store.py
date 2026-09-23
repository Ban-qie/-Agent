"""Controlled password accounts. No registration, external calls or default users."""
from contextlib import contextmanager
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from werkzeug.security import check_password_hash, generate_password_hash


class LoginRejected(Exception):
    pass


class LoginLimited(Exception):
    pass


class AccountStore:
    def __init__(self, path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        # Equal-cost verification for unknown users; never accepted as credentials.
        self._dummy = generate_password_hash(secrets.token_urlsafe(32), method='scrypt')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, '
                       'username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, '
                       'enabled INTEGER NOT NULL, auth_version INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS login_limits '
                       '(scope TEXT PRIMARY KEY, started REAL NOT NULL, attempts INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS auth_sessions (sid TEXT PRIMARY KEY, '
                       'owner TEXT NOT NULL REFERENCES accounts(id), version INTEGER NOT NULL, '
                       'expires REAL NOT NULL)')

    @staticmethod
    def validate(username, password):
        if (not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{3,32}', username)
                or not isinstance(password, str) or not 6 <= len(password) <= 30):
            raise ValueError('Invalid account credentials')
        try:
            if len(password.encode('utf-8')) > 512:
                raise ValueError('Invalid account credentials')
        except UnicodeError:
            raise ValueError('Invalid account credentials') from None

    def create(self, username, password):
        self.validate(username, password)
        hashed = generate_password_hash(password, method='scrypt')
        owner = uuid.uuid4().hex
        with self.transaction() as db:
            if db.execute('SELECT count(*) FROM accounts').fetchone()[0] >= 10:
                raise ValueError('Account capacity reached')
            db.execute('INSERT INTO accounts VALUES(?,?,?,1,1)', (owner, username, hashed))
        return owner

    def _rate_limit(self, username, source):
        now = self.clock()
        with self.transaction() as db:
            db.execute('DELETE FROM login_limits WHERE started <= ?', (now - 60,))
            scopes = [('global', 60), ('source:' + source, 10), ('account:' + username, 5)]
            for scope, maximum in scopes:
                row = db.execute('SELECT attempts FROM login_limits WHERE scope=?', (scope,)).fetchone()
                if row and row[0] >= maximum:
                    raise LoginLimited()
            for scope, _ in scopes:
                db.execute('INSERT INTO login_limits VALUES(?,?,1) '
                           'ON CONFLICT(scope) DO UPDATE SET attempts=login_limits.attempts+1', (scope, now))

    def login(self, username, password, source):
        # Bound even invalid login input before hashing or DB-key creation.
        valid = True
        try:
            self.validate(username, password)
        except ValueError:
            valid = False
        key = username if isinstance(username, str) and re.fullmatch(r'[a-zA-Z0-9_-]{3,32}', username) else '<invalid>'
        self._rate_limit(key, source[:64])
        with self.transaction() as db:
            row = db.execute('SELECT * FROM accounts WHERE username=?', (key,)).fetchone()
        matched = check_password_hash(row['password_hash'] if row else self._dummy,
                                      password if valid else '<invalid>')
        if not valid or not matched or not row or not row['enabled']:
            raise LoginRejected()
        # A concurrent password change/disable must invalidate this login too.
        return self.principal(row['id'], row['auth_version'])

    def principal(self, owner, version):
        with self.transaction() as db:
            row = db.execute('SELECT id,username,auth_version FROM accounts '
                             'WHERE id=? AND auth_version=? AND enabled=1', (owner, version)).fetchone()
        if not row:
            raise LoginRejected()
        return dict(row)

    def revoke(self, owner, *, disable=False):
        with self.transaction() as db:
            db.execute('UPDATE accounts SET auth_version=auth_version+1, '
                       'enabled=CASE WHEN ? THEN 0 ELSE enabled END WHERE id=?', (disable, owner))

    def change_password(self, owner, password):
        self.validate('valid', password)
        hashed = generate_password_hash(password, method='scrypt')
        with self.transaction() as db:
            db.execute('UPDATE accounts SET password_hash=?,auth_version=auth_version+1 WHERE id=?',
                       (hashed, owner))

    def open_session(self, sid, principal, expires):
        with self.transaction() as db:
            db.execute('DELETE FROM auth_sessions WHERE expires <= ?', (self.clock(),))
            # One active login per account bounds durable sessions and revokes old devices.
            db.execute('DELETE FROM auth_sessions WHERE owner=?', (principal['id'],))
            db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)',
                       (sid, principal['id'], principal['auth_version'], expires))

    def close_session(self, sid):
        with self.transaction() as db:
            db.execute('DELETE FROM auth_sessions WHERE sid=?', (sid,))

    def session_principal(self, sid, now):
        with self.transaction() as db:
            row = db.execute('SELECT a.id,a.username,a.auth_version FROM auth_sessions s '
                             'JOIN accounts a ON a.id=s.owner WHERE s.sid=? AND s.expires>? '
                             'AND a.enabled=1 AND a.auth_version=s.version', (sid, now)).fetchone()
        if row is None:
            raise LoginRejected()
        return dict(row)
