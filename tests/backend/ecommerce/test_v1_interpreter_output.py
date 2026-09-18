import json
from types import SimpleNamespace

import pytest

from data_formulator.ecommerce.v1_agents import invoke_v1_agent_graph
from devtools.v1_interpreter_diagnostic import classify
from tests.backend.ecommerce.test_v1_agents import Client, Executor, QUESTION, answers
from tests.backend.ecommerce.test_v1_acceptance import _state


@pytest.mark.parametrize('raw,reason', [
    ('{', 'invalid_json'), ('[]', 'non_object_json'),
    ('{"fact_ids":["scope"],"summary":"invented"}', 'invalid_fields'),
    ('{"fact_ids":"scope"}', 'non_list_ids'), ('{"fact_ids":[]}', 'invalid_id_count'),
    (json.dumps({'fact_ids': ['scope'] * 7}), 'invalid_id_count'),
    ('{"fact_ids":[1]}', 'non_string_id'), ('{"fact_ids":[{}]}', 'non_string_id'),
    ('{"fact_ids":["fake"]}', 'unknown_id'), ('{"fact_ids":["scope","scope"]}', 'duplicate_id'),
])
def test_invalid_output_preserves_tool_result_without_retry(raw, reason):
    class RawClient(Client):
        def get_completion(self, messages, **kwargs):
            if len(self.messages) < 2:
                return super().get_completion(messages, **kwargs)
            self.messages.append(messages)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])
    client, executor = RawClient(answers()[:2]), Executor()
    result = invoke_v1_agent_graph(_state(QUESTION, 'r02-output-001'), executor, client=client)
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'INVALID_AGENT_OUTPUT'
    assert result['verified_result']['values']['sales_amount'] == '123.45'
    assert not result.get('explanation') and not result.get('chart_spec')
    assert len(executor.calls) == 1 and len(client.messages) == 3
    assert classify(raw, {'scope': 'verified'}) == reason


def test_diagnostic_valid_and_truncated_outputs():
    assert classify('{"fact_ids":["scope"]}', {'scope': 'verified'}) == 'valid'
    assert classify('{"fact_ids":[', {}, 'length') == 'output_truncated'
    assert classify(None, {}) == 'non_text_content'
    assert classify('a' * 8193, {}) == 'content_too_large'


def test_verified_fact_groups_are_bounded_for_six_total_citations():
    from data_formulator.ecommerce.v1_agents import verified_facts
    facts = verified_facts({'state': 'success', 'values': {'sales_amount': '10', 'order_count': 1},
        'groups': [{'key': str(i), 'sales_amount': '1', 'order_count': 1} for i in range(10)]})
    assert len([key for key in facts if key.startswith('current.group.')]) == 5
