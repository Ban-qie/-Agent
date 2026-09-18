"""V1 final semantic, recovery, and restricted HTTP regressions (offline)."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.v1_normalization import normalize_question, NormalizationError
from data_formulator.ecommerce.v1_graph import invoke_v1_business_graph
from data_formulator.ecommerce.v1_workspace import V1WorkspaceStore
from data_formulator.datalake.workspace_manager import WorkspaceManager
from tests.backend.ecommerce.test_v1_acceptance import _state


@pytest.mark.parametrize('question', [
    '分析2018年1月销售额只看价格大于100的订单',
    '分析2018年1月销售额排除SP', '分析2018年1月销售额只看电子产品',
    '分析2018年1月销售额为什么下降', '分析2018年1月销售额按周',
    '比较2018年1月与2018年2月与2018年3月销售额',
    '分析2018年13月销售额', '分析2018年1月销售额前5',
    '分析2018年1月销售额按地区分组减少最多前3',
    '分析[2018-01-01,2018-02-01)与2018年3月销售额',
    '分析2018年1月至2018年3月销售额',
    '分析2018年1月销售额按地区分组按日显示',
    '分析2018年1月销售额按地区分组最高最低前3',
    '分析2018年1月销售额按地区分组最高前3前5',
])
def test_no_silent_condition_loss(question):
    with pytest.raises(NormalizationError):
        normalize_question(question)


def test_average_metric_does_not_accidentally_select_sales():
    result = normalize_question('分析2018年1月平均订单商品金额不含运费')
    assert result['metrics'] == ['average_order_amount']
    sorted_result = normalize_question('分析2018年1月平均订单商品金额按地区分组平均订单商品金额最高前3')
    assert sorted_result['sort']['field'] == 'average_order_amount'


def test_change_ranking_uses_all_groups_and_same_keys_for_both_periods():
    class Executor:
        def execute(self, identity, request):
            assert request.limit == 200  # Cannot pre-truncate to Top 2.
            def period(values):
                return {'state': 'success', 'groups': [{'key': key, 'sales_amount': str(amount)} for key, amount in values],
                        'total_groups': 3, 'truncated': False}
            return {'state': 'success', 'current': period([('AC', 2), ('RJ', 90), ('SP', 300)]),
                    'baseline': period([('AC', 1), ('RJ', 200), ('SP', 310)])}
    result = invoke_v1_business_graph(_state('比较2018年2月与2018年1月销售额按地区分组销售金额减少最多前2', 'v115-sort-001'), Executor())
    assert result['status'] == 'success'
    output = result['verified_result']
    assert [g['key'] for g in output['current']['groups']] == ['RJ', 'SP']
    assert [g['key'] for g in output['baseline']['groups']] == ['RJ', 'SP']
    assert output['ranking'] == [{'key': 'RJ', 'absolute': '-110.00'}, {'key': 'SP', 'absolute': '-10.00'}]


def test_incomplete_group_set_cannot_be_ranked():
    class Executor:
        def execute(self, *args):
            return {'state': 'success', 'truncated': True, 'groups': [{'key': 'A', 'sales_amount': '3'}]}
    result = invoke_v1_business_graph(_state('分析2018年1月销售额按日显示最高前3', 'v115-limit-001'), Executor())
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'RESOURCE_LIMIT'


def test_real_process_exit_releases_v1_lease_and_read_never_executes(tmp_path):
    script = '''
import os,sys
from pathlib import Path
from data_formulator.ecommerce.v1_workspace import V1WorkspaceStore
from data_formulator.datalake.workspace_manager import WorkspaceManager
store=V1WorkspaceStore(WorkspaceManager(Path(sys.argv[1])), 'local:crash')
with store.task_lease('v115-crash-001'):
    store.save_run(node_id='v115-crash-001', question='crash', conditions={}, status='running')
    assert store.read()['nodes'][0]['status']=='running'
    os._exit(7)
'''
    run = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, timeout=30)
    assert run.returncode == 7
    store = V1WorkspaceStore(WorkspaceManager(tmp_path), 'local:crash')
    assert store.read()['nodes'][0]['status'] == 'interrupted'
    from data_formulator.ecommerce.v1_service import analyze_v1
    response = analyze_v1({'request_id': 'v115-crash-001', 'user_question': 'crash'}, 'local:crash', tmp_path, store)
    assert response['state'] == 'interrupted'
    assert not (tmp_path / 'execution-audit.json').exists()


def test_terminal_cannot_be_rewritten_or_given_another_parent(tmp_path):
    store = V1WorkspaceStore(WorkspaceManager(tmp_path), 'local:test')
    store.save_run(node_id='v115-sealed-001', question='x', conditions={}, status='success')
    with pytest.raises(ToolError):
        store.save_run(node_id='v115-sealed-001', question='x', conditions={}, status='running')


def test_interpreter_failure_keeps_verified_partial_result():
    from data_formulator.ecommerce.v1_graph import invoke_v1_graph, v1_default_handlers
    from data_formulator.ecommerce.v1_runtime import business_handlers
    class Executor:
        def execute(self, *args):
            return {'state': 'success', 'values': {'order_count': 3}, 'groups': []}
    def fail(*args):
        raise RuntimeError('secret internal detail')
    result = invoke_v1_graph(_state('分析2018年1月订单数', 'v115-partial-001'),
        {**v1_default_handlers(), **business_handlers(Executor()), 'interpreter': fail})
    assert result['status'] == 'failed'
    assert result['verified_result']['values']['order_count'] == 3
    assert 'secret' not in str(result)
    assert not result.get('chart_spec')


@pytest.mark.parametrize('body', [{}, {'request_id': 'v115-bad-001'},
    {'request_id': 'v115-bad-001', 'user_question': 'x', 'parent_node_id': []},
    {'request_id': 'v115-bad-001', 'user_question': 'x', 'parent_node_id': '../outside'},
    {'request_id': 'v115-bad-001', 'user_question': 'x', 'sql': 'delete from orders'}])
def test_invalid_request_never_opens_workspace(body, tmp_path):
    from data_formulator.ecommerce.v1_service import analyze_v1
    with pytest.raises(ToolError) as exc:
        analyze_v1(body, 'local:test', tmp_path)
    assert exc.value.code == 'INVALID_REQUEST'


def test_v1_http_guards_and_failures_preserve_conditions_and_replay(tmp_path, monkeypatch):
    from devtools.run_local import configure_offline
    configure_offline()
    from data_formulator.app import app
    from data_formulator.auth import identity
    from data_formulator.routes import ecommerce as routes
    from data_formulator.ecommerce import v1_service
    store = V1WorkspaceStore(WorkspaceManager(tmp_path / 'workspaces'), 'local:final')
    monkeypatch.setattr(identity, '_localhost_identity', 'local:final')
    monkeypatch.setattr(identity, '_provider', None)
    monkeypatch.setattr(routes, '_v1_workspace', lambda _: store)
    monkeypatch.setenv('ECOMMERCE_ANALYSIS_ORCHESTRATOR', 'v1')
    calls = []
    class Executor:
        def execute(self, identity, request):
            calls.append(request)
            raise ToolError('EXECUTION_TIMEOUT', 'timeout')
    monkeypatch.setattr(v1_service, 'MetricExecutor', lambda _: Executor())
    from data_formulator.ecommerce.v1_agents import QwenTeam
    monkeypatch.setattr(QwenTeam, 'ask', lambda self, role, prompt, payload:
        {'action': 'analyze', 'canonical_question': '分析2018年1月销售额地区SP', 'question': ''}
        if role == 'planner' else {'decision': 'approve', 'question': ''})
    client = app.test_client()
    for route, method, headers in [('/api/ecommerce/workspace', 'GET', {'Origin': 'https://foreign.example'}),
        ('/api/ecommerce/workspace', 'GET', {'Host': 'foreign.example:5567'}),
        ('/api/agent/analyst-streaming', 'POST', {}), ('/api/ecommerce/workspace', 'POST', {})]:
        assert client.open(route, method=method, headers=headers).status_code == 403
    body = {'request_id': 'v115-http-001', 'user_question': '分析2018年1月销售额地区SP'}
    response = client.post('/api/ecommerce/analyze', json=body)
    assert response.status_code == 422
    assert response.json['state'] == 'failed'
    assert response.json['conditions']['regions'] == ['SP']
    assert client.post('/api/ecommerce/analyze', json=body).json == response.json
    assert client.post('/api/ecommerce/analyze', json={**body, 'user_question': '分析2018年2月销售额'}).status_code == 409
    assert len(calls) == 1
