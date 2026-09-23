import secrets

import pytest

from data_formulator.ecommerce.account_store import AccountStore
from data_formulator.ecommerce.multiuser_app import create_app


ORIGIN = "https://analytics.example.com"
PROXY_HEADERS = {
    "X-Forwarded-For": "198.51.100.7",
    "X-Forwarded-Host": "analytics.example.com",
    "X-Forwarded-Proto": "https",
}


def ready_checks(**overrides):
    checks = {name: (lambda: True) for name in ("database", "snapshot", "ledger")}
    checks.update(overrides)
    return checks


@pytest.fixture
def production(tmp_path):
    store = AccountStore(tmp_path / "multiuser.sqlite")
    store.initialize()
    store.create("alice", "test-password-A")
    app = create_app(tmp_path, secret_key=secrets.token_hex(32), profile="production",
                     origin=ORIGIN, trusted_proxy_cidrs=("127.0.0.0/8",),
                     trusted_proxy_hops=1, readiness_checks=ready_checks())
    app.config["TESTING"] = True
    return app


def proxied(client, path, *, headers=None, method="GET", json=None):
    merged = {**PROXY_HEADERS, **(headers or {})}
    return client.open(path, method=method, base_url="http://internal:5567",
                       headers=merged, json=json)


@pytest.mark.parametrize("origin", [
    "http://analytics.example.com",
    "https://analytics.example.com:444",
    "https://user@analytics.example.com",
    "https://analytics.example.com/path",
])
def test_production_origin_is_canonical_https(tmp_path, origin):
    store = AccountStore(tmp_path / "multiuser.sqlite")
    store.initialize()
    store.create("alice", "test-password-A")
    with pytest.raises(ValueError):
        create_app(tmp_path, secret_key=secrets.token_hex(32), profile="production",
                   origin=origin, trusted_proxy_cidrs=("127.0.0.0/8",), trusted_proxy_hops=1,
                   readiness_checks=ready_checks())


def test_production_requires_one_explicit_proxy_hop(tmp_path):
    store = AccountStore(tmp_path / "multiuser.sqlite")
    store.initialize()
    store.create("alice", "test-password-A")
    for hops, networks in [(0, ("127.0.0.0/8",)), (2, ("127.0.0.0/8",)), (1, ())]:
        with pytest.raises(ValueError):
            create_app(tmp_path, secret_key=secrets.token_hex(32), profile="production",
                       origin=ORIGIN, trusted_proxy_cidrs=networks, trusted_proxy_hops=hops,
                       readiness_checks=ready_checks())


def test_production_requires_all_readiness_checks(tmp_path):
    store = AccountStore(tmp_path / "multiuser.sqlite")
    store.initialize()
    store.create("alice", "test-password-A")
    for checks in (None, {}, {"database": lambda: True}):
        with pytest.raises(ValueError):
            create_app(tmp_path, secret_key=secrets.token_hex(32), profile="production",
                       origin=ORIGIN, trusted_proxy_cidrs=("127.0.0.0/8",),
                       trusted_proxy_hops=1, readiness_checks=checks)


def test_trusted_proxy_health_and_secure_cookie(production):
    client = production.test_client()
    assert proxied(client, "/healthz").json == {"status": "ok"}
    ready = proxied(client, "/readyz")
    assert ready.status_code == 200
    assert ready.json == {"status": "ready", "checks": {
        "config": True, "database": True, "snapshot": True, "ledger": True}}
    response = proxied(client, "/api/ecommerce/auth/status")
    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert "v4_session=" in cookie and "Secure" in cookie and "HttpOnly" in cookie


@pytest.mark.parametrize("headers", [
    {"X-Forwarded-Proto": "http"},
    {"X-Forwarded-Host": "attacker.invalid"},
    {"X-Forwarded-Port": "443"},
])
def test_bad_forwarding_metadata_is_rejected_before_flask(production, headers):
    response = proxied(production.test_client(), "/healthz", headers=headers)
    assert response.status_code == 403
    assert response.json["error"]["code"] == "ACCESS_DENIED"


@pytest.mark.parametrize("forwarded_for", ["", "not-an-ip", "198.51.100.7, 203.0.113.9"])
def test_forwarded_for_is_one_address(production, forwarded_for):
    response = proxied(production.test_client(), "/healthz",
                       headers={"X-Forwarded-For": forwarded_for})
    assert response.status_code == 403


def test_forwarded_headers_from_untrusted_source_are_rejected(production):
    # Override the WSGI peer, which must be checked before ProxyFix rewrites it.
    response = production.test_client().get(
        "/healthz", base_url="http://internal:5567", headers=PROXY_HEADERS,
        environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert response.status_code == 403


@pytest.mark.parametrize("failed_check", ["database", "snapshot", "ledger"])
def test_readiness_failure_is_503_without_details(tmp_path, failed_check):
    store = AccountStore(tmp_path / "multiuser.sqlite")
    store.initialize()
    store.create("alice", "test-password-A")
    failures = {failed_check: lambda: False}
    app = create_app(tmp_path, secret_key=secrets.token_hex(32), profile="production",
                     origin=ORIGIN, trusted_proxy_cidrs=("127.0.0.0/8",),
                     trusted_proxy_hops=1,
                     readiness_checks=ready_checks(**failures))
    response = proxied(app.test_client(), "/readyz")
    assert response.status_code == 503
    assert response.json["status"] == "unavailable"
    assert set(response.json["checks"]) == {"config", "database", "snapshot", "ledger"}
    assert response.json["checks"][failed_check] is False


def test_origin_csrf_host_and_default_api_deny(production):
    client = production.test_client()
    status = proxied(client, "/api/ecommerce/auth/status")
    csrf = status.json["csrf_token"]
    bad = proxied(client, "/api/ecommerce/auth/login", method="POST",
                  headers={"Origin": "https://attacker.invalid", "X-CSRF-Token": csrf},
                  json={"username": "alice", "password": "test-password-A"})
    assert bad.status_code == 403
    login = proxied(client, "/api/ecommerce/auth/login", method="POST",
                    headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                    json={"username": "alice", "password": "test-password-A"})
    assert login.status_code == 200
    denied = proxied(client, "/api/unknown", method="POST",
                     headers={"Origin": ORIGIN, "X-CSRF-Token": login.json["csrf_token"]}, json={})
    assert denied.status_code == 403
    assert denied.json["error"]["code"] == "TOOL_NOT_ALLOWED"
