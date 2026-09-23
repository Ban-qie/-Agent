import json
from pathlib import Path

import pytest

from devtools.service_resilience import FixtureIdentity, StubModelPlan


def test_fixture_identity_is_unique_and_confined(tmp_path):
    first = FixtureIdentity.create(tmp_path)
    second = FixtureIdentity.create(tmp_path)
    first.validate(tmp_path)
    second.validate(tmp_path)
    assert first.token != second.token
    assert first.schema != second.schema
    assert first.redis_namespace != second.redis_namespace
    assert first.runtime_root.is_relative_to(tmp_path)
    assert first.audit_root.is_relative_to(tmp_path)


def test_fixture_identity_rejects_paths_outside_root(tmp_path):
    identity = FixtureIdentity('abc123', 'v4_resilience_abc123',
                               'ecommerce:v4:resilience:abc123:',
                               Path(tmp_path.anchor) / 'outside', tmp_path / 'audit')
    with pytest.raises(ValueError, match='not isolated'):
        identity.validate(tmp_path)


def test_stub_model_is_deterministic_and_has_no_network_configuration():
    plan = StubModelPlan()
    client = plan.client()
    answers = []
    for _ in range(3):
        response = client.get_completion([{'role': 'user', 'content': 'fixture'}])
        answers.append(json.loads(response.choices[0].message.content))
        assert response.usage.prompt_tokens == response.usage.completion_tokens == 0
    assert plan.calls == 3
    assert answers[0]['action'] == 'analyze'
    assert answers[1]['decision'] == 'approve'
    assert answers[2]['fact_ids'] == ['current.sales_amount', 'scope']
    assert not hasattr(client, 'api_key') and not hasattr(client, 'api_base')
