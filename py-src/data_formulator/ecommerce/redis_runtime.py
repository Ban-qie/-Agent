"""Small bounded Redis adapter for sessions, rate limits, and coordination."""
from contextlib import contextmanager
import pickle
import re
import secrets
import socket
import time

from cachelib.base import BaseCache


class RedisUnavailable(RuntimeError):
    pass


class RedisClient:
    def __init__(self, host, port, password, *, database=0, timeout=0.5, namespace='ecommerce:v4:'):
        if (not isinstance(host, str) or not host or type(port) is not int or not 1 <= port <= 65535
                or not isinstance(password, str) or not password or type(database) is not int
                or not 0 <= database <= 15 or not isinstance(timeout, (int, float)) or not 0 < timeout <= 5
                or not isinstance(namespace, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,64}', namespace)):
            raise ValueError('Invalid Redis configuration')
        self.host, self.port, self.password = host, port, password
        self.database, self.timeout, self.namespace = database, float(timeout), namespace

    @staticmethod
    def _wire(parts):
        encoded = []
        for part in parts:
            if isinstance(part, bytes):
                value = part
            elif isinstance(part, (str, int)):
                value = str(part).encode('utf-8')
            else:
                raise TypeError('Redis command values must be bytes, strings, or integers')
            encoded.append(b'$' + str(len(value)).encode('ascii') + b'\r\n' + value + b'\r\n')
        return b'*' + str(len(encoded)).encode('ascii') + b'\r\n' + b''.join(encoded)

    @classmethod
    def _read(cls, stream):
        prefix = stream.read(1)
        if not prefix:
            raise RedisUnavailable('Redis closed the connection')
        line = stream.readline(1024 * 1024)
        if not line.endswith(b'\r\n'):
            raise RedisUnavailable('Invalid Redis response')
        value = line[:-2]
        if prefix == b'+':
            return value
        if prefix == b'-':
            raise RedisUnavailable('Redis rejected the operation')
        if prefix == b':':
            try:
                return int(value)
            except ValueError:
                raise RedisUnavailable('Invalid Redis integer') from None
        if prefix == b'$':
            try:
                length = int(value)
            except ValueError:
                raise RedisUnavailable('Invalid Redis bulk length') from None
            if length == -1:
                return None
            if not 0 <= length <= 1024 * 1024:
                raise RedisUnavailable('Redis response exceeds the byte limit')
            data = stream.read(length + 2)
            if len(data) != length + 2 or not data.endswith(b'\r\n'):
                raise RedisUnavailable('Truncated Redis response')
            return data[:-2]
        if prefix == b'*':
            try:
                count = int(value)
            except ValueError:
                raise RedisUnavailable('Invalid Redis array length') from None
            if not 0 <= count <= 1024:
                raise RedisUnavailable('Redis response exceeds the item limit')
            return [cls._read(stream) for _ in range(count)]
        raise RedisUnavailable('Unknown Redis response')

    def execute(self, *parts):
        try:
            with socket.create_connection((self.host, self.port), self.timeout) as connection:
                connection.settimeout(self.timeout)
                stream = connection.makefile('rwb', buffering=0)
                stream.write(self._wire(('AUTH', self.password)))
                if self._read(stream) != b'OK':
                    raise RedisUnavailable('Redis authentication failed')
                if self.database:
                    stream.write(self._wire(('SELECT', self.database)))
                    if self._read(stream) != b'OK':
                        raise RedisUnavailable('Redis database selection failed')
                stream.write(self._wire(parts))
                return self._read(stream)
        except RedisUnavailable:
            raise
        except (OSError, ValueError):
            raise RedisUnavailable('Redis is unavailable') from None

    def key(self, kind, value):
        if (not isinstance(kind, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', kind)
                or not isinstance(value, str) or not 1 <= len(value.encode('utf-8')) <= 256):
            raise ValueError('Invalid Redis key')
        return self.namespace + kind + ':' + value

    def ping(self):
        return self.execute('PING') == b'PONG'


class RedisCache(BaseCache):
    def __init__(self, client, *, default_timeout=1800):
        if not isinstance(client, RedisClient):
            raise TypeError('RedisClient required')
        super().__init__(default_timeout)
        self.client = client

    def _key(self, key):
        return self.client.key('session', key)

    def get(self, key):
        payload = self.client.execute('GET', self._key(key))
        if payload is None:
            return None
        if not isinstance(payload, bytes) or len(payload) > 64 * 1024:
            raise RedisUnavailable('Session payload exceeds the byte limit')
        try:
            value = pickle.loads(payload)
        except (pickle.PickleError, EOFError, TypeError, ValueError):
            raise RedisUnavailable('Session payload is invalid') from None
        if not isinstance(value, dict):
            raise RedisUnavailable('Session payload is invalid')
        return value

    def set(self, key, value, timeout=None):
        if not isinstance(value, dict):
            raise TypeError('Session value must be a dictionary')
        payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        if len(payload) > 64 * 1024:
            raise ValueError('Session payload exceeds the byte limit')
        ttl = int(self._normalize_timeout(timeout))
        if not 1 <= ttl <= 86400:
            raise ValueError('Session TTL is out of range')
        return self.client.execute('SET', self._key(key), payload, 'EX', ttl) == b'OK'

    def delete(self, key):
        return self.client.execute('DEL', self._key(key)) in (0, 1)

    def has(self, key):
        return self.client.execute('EXISTS', self._key(key)) == 1

    def clear(self):
        raise RedisUnavailable('Namespace-wide session deletion is disabled')


class RedisCoordinator:
    RATE_SCRIPT = """
for i = 1, #KEYS do
  local value = redis.call('INCR', KEYS[i])
  if value == 1 then redis.call('EXPIRE', KEYS[i], ARGV[(i - 1) * 2 + 1]) end
  if value > tonumber(ARGV[(i - 1) * 2 + 2]) then return 0 end
end
return 1
"""
    RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""

    def __init__(self, client):
        if not isinstance(client, RedisClient):
            raise TypeError('RedisClient required')
        self.client = client

    def rate_limit(self, scopes, *, window_seconds=60):
        if (not isinstance(scopes, (tuple, list)) or not scopes or not 1 <= window_seconds <= 3600
                or any(not isinstance(item, tuple) or len(item) != 2 or
                       not isinstance(item[0], str) or type(item[1]) is not int or item[1] < 1 for item in scopes)):
            raise ValueError('Invalid rate limit')
        keys = [self.client.key('rate', scope) for scope, _maximum in scopes]
        arguments = []
        for _scope, maximum in scopes:
            arguments.extend((window_seconds, maximum))
        return self.client.execute('EVAL', self.RATE_SCRIPT, len(keys), *keys, *arguments) == 1

    @contextmanager
    def lock(self, name, *, ttl_ms=5000, wait_ms=0):
        if not 100 <= ttl_ms <= 60000 or not 0 <= wait_ms <= 5000:
            raise ValueError('Invalid lock TTL')
        key = self.client.key('lock', name)
        token = secrets.token_hex(16)
        deadline = time.monotonic() + wait_ms / 1000
        while self.client.execute('SET', key, token, 'NX', 'PX', ttl_ms) != b'OK':
            if time.monotonic() >= deadline:
                raise RedisUnavailable('Coordination lock is busy')
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            self.client.execute('EVAL', self.RELEASE_SCRIPT, 1, key, token)
