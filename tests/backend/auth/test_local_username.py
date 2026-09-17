"""Regression coverage for local Windows account names (no external calls)."""
import flask
import pytest

import data_formulator.auth.identity as identity


@pytest.fixture
def local_app(monkeypatch):
    monkeypatch.setattr(identity, "_provider", None)
    monkeypatch.setattr(identity, "_localhost_identity", None)
    monkeypatch.setattr(identity, "_allow_anonymous", True)
    monkeypatch.setenv("AUTH_PROVIDER", "")
    app = flask.Flask(__name__)
    app.config["CLI_ARGS"] = {"host": "127.0.0.1"}
    return app


@pytest.mark.parametrize("username", ["Kobe Bryant", "本机用户", "alice"])
def test_local_identity_stable_and_ignores_headers(local_app, monkeypatch, username):
    monkeypatch.setattr(identity.getpass, "getuser", lambda: username)
    identity.init_auth(local_app)
    assert identity.is_local_mode()
    with local_app.test_request_context():
        expected = identity.get_identity_id()
    identity.init_auth(local_app)
    with local_app.test_request_context(headers={"X-Identity-Id": "user:someone-else"}):
        assert identity.get_identity_id() == expected
    assert expected.startswith("local:")
    assert identity._validate_identity_value(expected, "test") == expected
    if username == "alice":
        assert expected == "local:alice"


def test_different_space_names_do_not_collapse(local_app, monkeypatch):
    ids = []
    for name in ("a b", "a  b", "ab"):
        monkeypatch.setattr(identity.getpass, "getuser", lambda: name)
        identity.init_auth(local_app)
        with local_app.test_request_context():
            ids.append(identity.get_identity_id())
    assert len(set(ids)) == 3


def test_external_host_still_rejects_spaces(local_app, monkeypatch):
    local_app.config["CLI_ARGS"]["host"] = "0.0.0.0"
    monkeypatch.setattr(identity.getpass, "getuser", lambda: "Kobe Bryant")
    identity.init_auth(local_app)
    assert not identity.is_local_mode()
    with local_app.test_request_context(headers={"X-Identity-Id": "Kobe Bryant"}):
        with pytest.raises(ValueError):
            identity.get_identity_id()


def test_unavailable_os_username_falls_back(local_app, monkeypatch):
    monkeypatch.setattr(identity.getpass, "getuser", lambda: "")
    identity.init_auth(local_app)
    assert not identity.is_local_mode()
