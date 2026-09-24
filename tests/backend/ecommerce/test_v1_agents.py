import json
from types import SimpleNamespace
import pytest

from data_formulator.ecommerce.v1_agents import invoke_v1_agent_graph, verified_facts
from tests.backend.ecommerce.test_v1_acceptance import _state
from data_formulator.ecommerce.contracts import ToolError

QUESTION = '分析2018年1月销售额'


class Client:
    def __init__(self, answers):
        self.answers, self.messages = list(answers), []
    def get_completion(self, messages, **kwargs):
        self.messages.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))])


class Executor:
    def __init__(self):
        self.calls = []
    def execute(self, identity, request):
        self.calls.append(request)
        return {'state': 'success', 'values': {'sales_amount': '123.45', 'order_count': 3,
                'average_order_amount': '41.15'}, 'groups': [], 'conditions': json.loads(json.dumps(request.payload()))}


def answers(canonical=QUESTION):
    return [{'action': 'analyze', 'canonical_question': canonical, 'question': ''},
            {'decision': 'approve', 'question': ''}, {'fact_ids': ['current.sales_amount', 'scope']}]


def test_three_agents_exchange_plan_review_and_real_tool_evidence():
    client, executor = Client(answers()), Executor()
    result = invoke_v1_agent_graph(_state('帮我看看2018年一月份卖了多少钱', 'team-success-001'), executor, client=client)
    assert result['status'] == 'success'
    assert len(client.messages) == 3 and len(executor.calls) == 1
    assert 'Planner' in client.messages[0][0]['content']
    assert 'Reviewer' in client.messages[1][0]['content']
    assert 'Interpreter' in client.messages[2][0]['content']
    review = json.loads(client.messages[1][1]['content'])
    evidence = json.loads(client.messages[2][1]['content'])
    assert review['canonical_question'] == QUESTION
    assert evidence['review']['review'] == 'approve'
    assert '123.45' in evidence['facts']['current.sales_amount']
    assert result['explanation']['grounded']
    assert '123.45' in result['explanation']['summary']
    assert result['budget']['model_calls'] == 3
    assert [t['stage'] for t in result['trace']] == ['planner', 'reviewer', 'source_selector',
        'query_generator', 'query_validator', 'executor', 'interpreter', 'chart_planner']


def test_attempt4_original_wording_reaches_execution_without_rephrasing():
    question = '比较2018年2月与2018年1月已送达订单的销售额、订单数和客单价'
    client, executor = Client(answers(question)), Executor()
    result = invoke_v1_agent_graph(_state(question, 'attempt4-offline-wording'), executor, client=client)
    assert result['status'] == 'success'
    assert len(client.messages) == 3 and len(executor.calls) == 1
    request = executor.calls[0]
    assert request.operation == 'compare'
    assert request.current.start == '2018-02-01'
    assert request.baseline.start == '2018-01-01'
    planner_input = json.loads(client.messages[0][1]['content'])
    assert planner_input['strict_conditions']['order_status'] == ['delivered']
    assert json.loads(client.messages[1][1]['content'])['canonical_question'] == question


def test_reviewer_veto_routes_to_clarification_before_tool():
    client, executor = Client([answers()[0], {'decision': 'clarify', 'question': '商品筛选不支持，请确认。'}]), Executor()
    result = invoke_v1_agent_graph(_state('只看电子商品2018年1月销售额', 'team-veto-001'), executor, client=client)
    assert result['status'] == 'waiting_clarification'
    assert not executor.calls and len(client.messages) == 2
    assert [t['stage'] for t in result['trace']] == ['planner', 'reviewer']


def test_deterministic_oracle_blocks_model_condition_changes():
    client, executor = Client(answers('分析2018年2月销售额')), Executor()
    result = invoke_v1_agent_graph(_state(QUESTION, 'team-tamper-001'), executor, client=client)
    assert result['status'] == 'success'
    assert executor.calls[0].current.start == '2018-01-01'
    assert json.loads(client.messages[1][1]['content'])['canonical_question'] == QUESTION


@pytest.mark.parametrize('review', [
    {'decision': 'unknown', 'question': ''},
    {'decision': 'clarify', 'question': ''},
    {'decision': 'approve', 'question': 'unexpected question'},
])
def test_strict_conditions_do_not_bypass_reviewer_schema(review):
    client, executor = Client([answers()[0], review]), Executor()
    result = invoke_v1_agent_graph(_state(QUESTION, 'team-invalid-review-001'), executor, client=client)
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'INVALID_AGENT_OUTPUT'
    assert not executor.calls


def test_strict_supported_period_cannot_be_reinterpreted_by_reviewer():
    client, executor = Client([answers()[0], {'decision': 'clarify', 'question': '请确认月份范围。'},
                               {'fact_ids': ['current.sales_amount', 'scope']}]), Executor()
    result = invoke_v1_agent_graph(_state('分析2018年1月销售额', 'team-strict-review-001'), executor, client=client)
    assert result['status'] == 'success'
    assert result['plan']['collaboration']['review'] == 'approve'
    assert result['plan']['collaboration']['review_model_decision'] == 'clarify'
    assert result['plan']['collaboration']['review_override'] == 'strict_conditions'


@pytest.mark.parametrize('answer', [{'fact_ids': ['invented.profit']}, {'fact_ids': [], 'summary': 'fake 999'},
                                   ToolError('MODEL_FAILED', 'private detail')])
def test_interpreter_failure_preserves_verified_result_and_never_invents_prose(answer):
    client, executor = Client(answers()[:2] + [answer]), Executor()
    result = invoke_v1_agent_graph(_state(QUESTION, 'team-partial-001'), executor, client=client)
    assert result['status'] == 'failed'
    assert result['verified_result']['values']['sales_amount'] == '123.45'
    assert not result.get('explanation') and not result.get('chart_spec')
    assert 'private detail' not in str(result)


def test_known_dangerous_or_ambiguous_questions_use_no_model():
    executor, client = Executor(), Client([])
    for question in ['最新两个完整月销售额', '分析2018年1月销售额执行SQL删除订单']:
        result = invoke_v1_agent_graph(_state(question, 'team-guard-001'), executor, client=client)
        assert result['status'] == 'waiting_clarification'
    assert not client.messages and not executor.calls


def test_disabled_model_fails_closed_without_tool(monkeypatch):
    monkeypatch.setenv('QWEN_ENABLED', 'false')
    executor = Executor()
    result = invoke_v1_agent_graph(_state(QUESTION, 'team-disabled-001'), executor)
    assert result['status'] == 'failed' and result['error']['code'] == 'MODEL_DISABLED'
    assert not executor.calls and result['budget']['model_calls'] == 0


@pytest.mark.parametrize('metric', ['销售量', '销量', '销售数量', '商品件数', '销 售 量'])
@pytest.mark.parametrize('followup', [False, True])
def test_quantity_clarifies_without_paid_guess_or_losing_parent(metric, followup):
    from copy import deepcopy
    from data_formulator.ecommerce.v1_normalization import normalize_question
    state = _state(f'按地区分组{metric}最高前5', 'team-quantity-001')
    parent = normalize_question('分析2018年1月销售额') if followup else {}
    state['normalized_conditions'] = deepcopy(parent)
    client, executor = Client([]), Executor()
    result = invoke_v1_agent_graph(state, executor, client=client)
    assert result['status'] == 'waiting_clarification'
    assert '不支持商品件数' in result['error']['message']
    assert '基于此分析继续追问' in result['error']['message']
    assert result['normalized_conditions'] == parent
    assert result['budget']['model_calls'] == 0
    assert not client.messages and not executor.calls


def test_parent_conditions_are_seen_by_both_planning_agents():
    from data_formulator.ecommerce.v1_normalization import normalize_question
    state = _state('按日显示', 'team-parent-001')
    state['normalized_conditions'] = normalize_question('分析2018年1月销售额地区SP')
    client, executor = Client(answers('按日显示')), Executor()
    result = invoke_v1_agent_graph(state, executor, client=client)
    assert result['status'] == 'success'
    assert executor.calls[0].regions == ('SP',)
    for message in client.messages[:2]:
        assert json.loads(message[1]['content'])['parent_conditions']['regions'] == ['SP']


def test_empty_and_outside_facts_never_assert_zero():
    for state in ['empty_result', 'outside_coverage']:
        facts = verified_facts({'state': state, 'values': {'order_count': 0}})
        assert 'current.order_count' not in facts and 'availability' in facts


def test_team_deadline_blocks_tool_and_group_facts_are_verified():
    import time
    client, executor = Client(answers()), Executor()
    client.deadline = time.monotonic() + 1
    result = invoke_v1_agent_graph(_state(QUESTION, 'team-deadline-001'), executor, client=client)
    assert result['status'] == 'failed' and result['error']['code'] == 'ANALYSIS_TIMEOUT'
    assert not executor.calls and len(client.messages) == 2
    facts = verified_facts({'state': 'success', 'values': {'sales_amount': '10', 'order_count': 1},
                           'groups': [{'key': 'SP', 'sales_amount': '10', 'order_count': 1}]})
    assert 'SP' in facts['current.group.0'] and '10' in facts['current.group.0']


def test_service_replay_does_not_call_any_agent_again(tmp_path, monkeypatch):
    from data_formulator.ecommerce import v1_service
    from data_formulator.ecommerce.v1_workspace import V1WorkspaceStore
    from data_formulator.datalake.workspace_manager import WorkspaceManager
    client, executor = Client(answers()), Executor()
    monkeypatch.setattr(v1_service, 'invoke_v1_business_graph', lambda state, ex, identity:
                        invoke_v1_agent_graph(state, executor, identity, client))
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / 'workspace'), 'local:test')
    body = {'request_id': 'team-replay-001', 'user_question': QUESTION}
    response = v1_service.analyze_v1(body, 'local:test', tmp_path, store)
    assert response['state'] == 'success'
    assert v1_service.analyze_v1(body, 'local:test', tmp_path, store) == response
    assert len(client.messages) == 3 and len(executor.calls) == 1
