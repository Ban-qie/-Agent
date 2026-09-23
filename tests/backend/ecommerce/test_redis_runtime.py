import pickle

import pytest

from data_formulator.ecommerce.redis_runtime import RedisCache, RedisClient, RedisCoordinator, RedisUnavailable
from data_formulator.ecommerce.multiuser_app import StorageFailureMiddleware


class FakeRedis(RedisClient):
    def __init__(self):
        self.namespace = 'test:'
        self.values = {}
        self.calls = []

    def execute(self, *parts):
        self.calls.append(parts)
        command = parts[0]
        if command == 'GET':
            return self.values.get(parts[1])
        if command == 'SET':
            key, value = parts[1], parts[2]
            if 'NX' in parts and key in self.values:
                return None
            self.values[key] = value
            return b'OK'
        if command == 'DEL':
            return int(self.values.pop(parts[1], None) is not None)
        if command == 'EXISTS':
            return int(parts[1] in self.values)
        if command == 'EVAL' and parts[1] == RedisCoordinator.RATE_SCRIPT:
            return 1
        if command == 'EVAL':
            self.values.pop(parts[3], None)
            return 1
        raise AssertionError(parts)


def test_cache_roundtrip_bounds_and_no_clear():
    client = FakeRedis()
    cache = RedisCache(client)
    assert cache.set('sid', {'owner': 'alice'}, timeout=30)
    assert cache.get('sid') == {'owner': 'alice'}
    assert cache.has('sid')
    assert cache.delete('sid') and cache.get('sid') is None
    with pytest.raises(ValueError):
        cache.set('large', {'data': 'x' * (65 * 1024)})
    with pytest.raises(RedisUnavailable):
        cache.clear()


def test_cache_rejects_invalid_payload_and_keys():
    client = FakeRedis()
    cache = RedisCache(client)
    client.values[client.key('session', 'broken')] = pickle.dumps(['not', 'a', 'dict'])
    with pytest.raises(RedisUnavailable):
        cache.get('broken')
    with pytest.raises(ValueError):
        client.key('session', '')


def test_coordinator_rate_and_owner_checked_lock():
    client = FakeRedis()
    coordinator = RedisCoordinator(client)
    assert coordinator.rate_limit([('global', 10), ('owner:alice', 5)])
    with coordinator.lock('submit'):
        with pytest.raises(RedisUnavailable):
            with coordinator.lock('submit'):
                pass
    assert client.key('lock', 'submit') not in client.values


def test_client_rejects_bad_configuration_and_sanitizes_connection_error():
    with pytest.raises(ValueError):
        RedisClient('', 6379, 'secret')
    client = RedisClient('127.0.0.1', 1, 'do-not-leak', timeout=0.01)
    with pytest.raises(RedisUnavailable) as error:
        client.ping()
    assert 'do-not-leak' not in str(error.value)


def test_wsgi_storage_failure_before_request_context_is_sanitized():
    def broken(_environ, _start_response):
        raise RedisUnavailable('synthetic-token-must-not-leak')

    statuses = []
    headers = []
    response = StorageFailureMiddleware(broken)(
        {}, lambda status, values: (statuses.append(status), headers.extend(values)))
    payload = b''.join(response)
    assert statuses == ['503 Service Unavailable']
    assert b'STORAGE_UNAVAILABLE' in payload
    assert b'synthetic-token-must-not-leak' not in payload
    assert ('Cache-Control', 'no-store') in headers
