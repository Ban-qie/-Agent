"""PostgreSQL-backed durable V4 task store with Redis coordination."""
from contextlib import contextmanager
import re
import secrets
import threading
import time

import psycopg2
from psycopg2.extras import DictCursor
from psycopg2.pool import ThreadedConnectionPool

from data_formulator.ecommerce.account_store import LoginLimited
from data_formulator.ecommerce.contracts import StorageUnavailable
from data_formulator.ecommerce.task_store import TaskStore


class CursorAdapter:
    def __init__(self, cursor):
        self.cursor = cursor

    @staticmethod
    def sql(statement):
        statement = statement.replace('?', '%s')
        statement = statement.replace('ORDER BY rowid', 'ORDER BY position')
        statement = statement.replace('sum(max(reserved,estimated))',
                                      'sum(greatest(reserved,estimated))')
        if statement.startswith('INSERT INTO nodes VALUES('):
            statement = statement.replace('INSERT INTO nodes VALUES(',
                'INSERT INTO nodes(owner,workspace,id,parent,payload,bytes) VALUES(', 1)
        return statement

    def execute(self, statement, parameters=()):
        self.cursor.execute(self.sql(statement), parameters)
        return self

    def executemany(self, statement, parameters):
        self.cursor.executemany(self.sql(statement), parameters)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def __iter__(self):
        return iter(self.cursor)


class PostgresTaskStore(TaskStore):
    def __init__(self, dsn, *, schema='public', clock=time.time, coordinator=None,
                 max_connections=5, max_active_tasks=1):
        if (not isinstance(dsn, str) or not dsn or not isinstance(schema, str)
                or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema)
                or type(max_connections) is not int or not 2 <= max_connections <= 20
                or type(max_active_tasks) is not int or not 1 <= max_active_tasks <= 2):
            raise ValueError('Invalid PostgreSQL configuration')
        self.dsn, self.schema, self.clock = dsn, schema, clock
        self.coordinator = coordinator
        self.max_active_tasks = max_active_tasks
        self.identity = 'postgresql:' + schema
        try:
            self.pool = ThreadedConnectionPool(
                1, max_connections, dsn=dsn, connect_timeout=2,
                application_name='agent-ecommerce-v4',
                options='-c statement_timeout=3000 -c lock_timeout=1500')
        except psycopg2.Error:
            raise StorageUnavailable('PostgreSQL is unavailable') from None
        self._pool_slots = threading.BoundedSemaphore(max_connections)
        # Preserve equal-cost verification for unknown users without exposing configuration.
        from werkzeug.security import generate_password_hash
        self._dummy = generate_password_hash(secrets.token_urlsafe(32), method='scrypt')

    @contextmanager
    def transaction(self):
        connection = None
        acquired = self._pool_slots.acquire(timeout=1)
        if not acquired:
            raise StorageUnavailable('PostgreSQL connection pool is busy')
        try:
            connection = self.pool.getconn()
            connection.autocommit = False
            cursor = connection.cursor(cursor_factory=DictCursor)
            cursor.execute('SET LOCAL search_path TO "' + self.schema + '"')
            # Low-volume release target: serialize state transitions across app instances.
            cursor.execute('SELECT pg_advisory_xact_lock(846302905)')
            yield CursorAdapter(cursor)
            connection.commit()
        except psycopg2.Error:
            if connection is not None:
                connection.rollback()
            raise StorageUnavailable('PostgreSQL operation failed') from None
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                self.pool.putconn(connection)
            self._pool_slots.release()

    def initialize(self):
        statements = [
            'CREATE TABLE IF NOT EXISTS accounts ('
            'id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,'
            'enabled INTEGER NOT NULL,auth_version INTEGER NOT NULL)',
            'CREATE TABLE IF NOT EXISTS login_limits ('
            'scope TEXT PRIMARY KEY,started DOUBLE PRECISION NOT NULL,attempts INTEGER NOT NULL)',
            'CREATE TABLE IF NOT EXISTS auth_sessions ('
            'sid TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES accounts(id),'
            'version INTEGER NOT NULL,expires DOUBLE PRECISION NOT NULL)',
            'CREATE TABLE IF NOT EXISTS workspaces ('
            'owner TEXT NOT NULL REFERENCES accounts(id),id TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0,'
            'PRIMARY KEY(owner,id),UNIQUE(owner))',
            'CREATE TABLE IF NOT EXISTS nodes ('
            'owner TEXT NOT NULL,workspace TEXT NOT NULL,id TEXT NOT NULL,parent TEXT,payload TEXT NOT NULL,'
            'bytes INTEGER NOT NULL,position BIGSERIAL UNIQUE,PRIMARY KEY(owner,workspace,id),'
            'FOREIGN KEY(owner,workspace) REFERENCES workspaces(owner,id),'
            'FOREIGN KEY(owner,workspace,parent) REFERENCES nodes(owner,workspace,id))',
            'CREATE TABLE IF NOT EXISTS tasks ('
            'id TEXT PRIMARY KEY,owner TEXT NOT NULL,workspace TEXT NOT NULL,request_id TEXT NOT NULL,'
            'fingerprint TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,version INTEGER NOT NULL,'
            'lease_token TEXT,lease_expiry DOUBLE PRECISION,cancel_requested INTEGER NOT NULL DEFAULT 0,'
            'response TEXT,created DOUBLE PRECISION NOT NULL,updated DOUBLE PRECISION NOT NULL,'
            'UNIQUE(owner,workspace,request_id),'
            'FOREIGN KEY(owner,workspace) REFERENCES workspaces(owner,id))',
            'CREATE TABLE IF NOT EXISTS submit_limits ('
            'scope TEXT PRIMARY KEY,started DOUBLE PRECISION NOT NULL,attempts INTEGER NOT NULL)',
        ]
        with self.transaction() as database:
            for statement in statements:
                database.execute(statement)

    def _rate_limit(self, username, source):
        if self.coordinator is None:
            raise StorageUnavailable('Redis coordination is required')
        allowed = self.coordinator.rate_limit([
            ('login:global', 60), ('login:source:' + source[:64], 10),
            ('login:account:' + username, 5)], window_seconds=60)
        if not allowed:
            raise LoginLimited()

    def create_or_get(self, owner, workspace, body):
        if self.coordinator is None:
            raise StorageUnavailable('Redis coordination is required')
        with self.coordinator.lock('task-submit', ttl_ms=5000, wait_ms=1000):
            return super().create_or_get(owner, workspace, body)

    def recover_expired(self):
        if self.coordinator is None:
            raise StorageUnavailable('Redis coordination is required')
        with self.coordinator.lock('task-recovery', ttl_ms=5000):
            return super().recover_expired()

    def interrupt_inflight(self):
        if self.coordinator is None:
            raise StorageUnavailable('Redis coordination is required')
        with self.coordinator.lock('task-startup-recovery', ttl_ms=5000):
            return super().interrupt_inflight()

    def close(self):
        self.pool.closeall()
