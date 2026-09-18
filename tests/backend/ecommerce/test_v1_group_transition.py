from copy import deepcopy

import pytest

from data_formulator.ecommerce.v1_normalization import normalize_question
from data_formulator.ecommerce.v1_graph import invoke_v1_business_graph
from data_formulator.ecommerce.executor import MetricExecutor
from tests.backend.ecommerce.test_v1_acceptance import _state


ROOT_QUESTION = '比较2018年2月与2018年1月销售额、订单数、客单价'


@pytest.mark.parametrize('group', ['日', '月'])
def test_group_change_clears_old_rank_but_preserves_filters_and_parent(group):
    root = normalize_question(ROOT_QUESTION)
    ranked = normalize_question('按地区分组销售金额减少最多前3', root)
    original = deepcopy(ranked)
    trend = normalize_question(f'地区SP按{group}显示销售额', ranked)
    assert trend['sort'] is None and trend['top_n'] is None
    assert trend['current'] == root['current'] and trend['baseline'] == root['baseline']
    assert trend['regions'] == ['SP'] and trend['metrics'] == ['sales_amount']
    assert ranked == original
    sibling = normalize_question('按地区比较订单数', root)
    assert sibling['regions'] == [] and sibling['sort'] is None and sibling['top_n'] is None


def test_same_group_keeps_rank_and_explicit_new_rank_wins():
    parent = normalize_question(ROOT_QUESTION + '按地区分组销售金额减少最多前3')
    same = normalize_question('地区SP', parent)
    assert same['sort'] == parent['sort'] and same['top_n'] == 3
    daily = normalize_question('按日显示销售额最高前5', parent)
    assert daily['sort'] == {'field': 'sales_amount', 'direction': 'desc'}
    assert daily['top_n'] == 5


def test_real_worker_trend_after_region_rank_has_all_dates_and_no_fake_deltas(tmp_path):
    parent = normalize_question(ROOT_QUESTION + '按地区分组销售金额减少最多前3')
    state = _state('地区SP按日显示销售额', 'r03-day-trend-001')
    state['normalized_conditions'] = parent
    result = invoke_v1_business_graph(state, MetricExecutor(tmp_path / 'audit.json'))
    assert result['status'] == 'success'
    output = result['verified_result']
    assert not output.get('ranking')
    assert result['chart_spec']['type'] == 'line'
    for period, count, prefix in [('current', 28, '2018-02-'), ('baseline', 31, '2018-01-')]:
        keys = [row['key'] for row in output[period]['groups']]
        assert len(keys) == count and keys == sorted(keys)
        assert all(key.startswith(prefix) for key in keys)
        assert output[period]['truncated'] is False
