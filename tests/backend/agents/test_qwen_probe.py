"""Offline checks for V0-2 credentials, limits, usage and stream failures."""
import json
import os
from types import SimpleNamespace as NS

import pytest

from devtools.qwen_config import API_BASE, MODEL_ID, configure_qwen
from devtools.qwen_smoke import Ledger, MAX_ATTEMPTS, bounded_client


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("OTHER_ENABLED", "true")
    monkeypatch.setenv("OTHER_API_KEY", "fake-other-secret")
    configure_qwen("fake-qwen-secret-for-tests")
    from data_formulator.model_registry import ModelRegistry
    return ModelRegistry().get_config(MODEL_ID)


def test_public_api_and_client_resolve_only_server_key(config, monkeypatch):
    from data_formulator.model_registry import ModelRegistry
    from data_formulator.app import app
    from data_formulator.routes import agents
    registry = ModelRegistry()
    monkeypatch.setattr(agents, "model_registry", registry)
    assert os.environ["OTHER_ENABLED"] == "false"
    assert "OTHER_API_KEY" not in os.environ
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    response = app.test_client().get("/api/agent/list-global-models")
    assert response.status_code == 200
    assert config["api_key"] not in response.get_data(as_text=True)
    data = response.get_json()["data"]
    assert len(data) == 1 and data[0]["id"] == MODEL_ID
    assert "api_key" not in data[0]
    client = agents.get_client({"is_global": True, "id": MODEL_ID,
                                "api_key": "fake-client-key", "api_base": "https://invalid.example"})
    assert client.params["api_key"] == config["api_key"]
    assert client.params["api_base"] == API_BASE


def test_limits_usage_and_persistent_attempt_cap(config, tmp_path, monkeypatch):
    import litellm
    seen = []
    def completion(**kwargs):
        seen.append(kwargs)
        return NS(usage=NS(prompt_tokens=10, completion_tokens=5))
    monkeypatch.setattr(litellm, "completion", completion)
    path = tmp_path / "usage.json"
    client = bounded_client(config, Ledger(path))
    for _ in range(MAX_ATTEMPTS):
        client.get_completion([{"role": "user", "content": "OK"}], max_tokens=999999,
                              timeout=9999, num_retries=50)
    with pytest.raises(RuntimeError, match="budget"):
        bounded_client(config, Ledger(path)).get_completion([])
    assert len(seen) == MAX_ATTEMPTS
    for kwargs in seen:
        assert kwargs["max_tokens"] == 256 and kwargs["timeout"] == 15
        assert kwargs["num_retries"] == kwargs["max_retries"] == 0
        assert kwargs["enable_thinking"] is False
    rows = json.loads(path.read_text())
    assert rows[0]["estimated_cny"] == pytest.approx(0.000009)
    assert config["api_key"] not in path.read_text()


def test_oversized_input_rejected_before_reservation(config, tmp_path):
    ledger = Ledger(tmp_path / "usage.json")
    client = bounded_client(config, ledger)
    with pytest.raises(ValueError, match="byte cap"):
        client.get_completion([{"role": "user", "content": "x" * 5000}])
    assert ledger.rows == []


def test_probe_preflight_rejects_insufficient_budget_before_reading_key(tmp_path, monkeypatch):
    from devtools import qwen_smoke
    path = tmp_path / "usage.json"
    ledger = Ledger(path)
    for _ in range(MAX_ATTEMPTS - 1):
        ledger.reserve()
    monkeypatch.setattr(qwen_smoke, "LEDGER", path)
    def no_key_read():
        pytest.fail("Credential must not be read when probe budget is insufficient")
    monkeypatch.setattr(qwen_smoke, "read_user_key", no_key_read)
    with pytest.raises(RuntimeError, match="Insufficient"):
        qwen_smoke.probe(tools_only=True)


def test_error_redacted_and_attempt_preserved(config, tmp_path, monkeypatch):
    import litellm
    def failure(**kwargs):
        raise ValueError(config["api_key"])
    monkeypatch.setattr(litellm, "completion", failure)
    path = tmp_path / "usage.json"
    with pytest.raises(RuntimeError) as exc:
        bounded_client(config, Ledger(path)).get_completion([])
    assert config["api_key"] not in str(exc.value)
    assert config["api_key"] not in path.read_text()
    assert json.loads(path.read_text())[0]["state"] == "error_or_unknown"


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("async_close", [False, True])
def test_stream_usage_and_interruption(config, tmp_path, monkeypatch, fail, async_close):
    import litellm
    class Stream:
        closed = False
        def __iter__(self):
            yield NS(usage=None, choices=[])
            if fail:
                raise TimeoutError(config["api_key"])
            yield NS(usage=NS(prompt_tokens=30, completion_tokens=10), choices=[])
        def close(self):
            self.closed = True
    stream = Stream()
    if async_close:
        async def aclose():
            stream.closed = True
        stream.aclose = aclose
        stream.close = None
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: stream)
    path = tmp_path / "usage.json"
    output = bounded_client(config, Ledger(path)).get_completion_with_tools([], [], stream=True)
    if fail:
        with pytest.raises(RuntimeError):
            list(output)
    else:
        assert len(list(output)) == 2
    assert stream.closed
    row = json.loads(path.read_text())[0]
    assert row["state"] == ("error_or_unknown" if fail else "response_received")
    if not fail:
        assert row["input_tokens"] == 30 and row["output_tokens"] == 10
    assert config["api_key"] not in path.read_text()
