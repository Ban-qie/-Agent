"""Offline original-smoke wording through graph, real snapshot and fixed worker.

The model replies are explicit fixtures, not recorded or new Qwen responses.
Run in a network-none production container; no secrets or production volumes.
"""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['PYTHON_DOTENV_DISABLED'] = '1'

from data_formulator.ecommerce.executor import MetricExecutor, accepted_catalog, SNAPSHOT_ID
from data_formulator.ecommerce.v1_agents import invoke_v1_agent_graph
from data_formulator.ecommerce.v1_normalization import normalize_question, NormalizationError

QUESTION = '比较2018年2月与2018年1月已送达订单的销售额、订单数和客单价'


class FixtureClient:
    def __init__(self):
        self.calls = 0

    def get_completion(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            assert json.loads(messages[1]['content'])['strict_conditions']['order_status'] == ['delivered']
            answer = {'action': 'analyze', 'canonical_question': QUESTION, 'question': ''}
        elif self.calls == 2:
            answer = {'decision': 'approve', 'question': ''}
        else:
            assert self.calls == 3
            answer = {'fact_ids': ['current.sales_amount', 'baseline.sales_amount', 'change.sales_amount', 'scope']}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))])


def main():
    conditions = normalize_question(QUESTION)
    assert conditions == normalize_question(QUESTION.replace('已送达', '已交付'))
    rejected = 0
    for phrase in ('未送达订单', '排除已送达订单', '已送达订单或已取消订单', '已送达订单含运费', '已送达订单只看电子商品', '已送达订单按送达时间'):
        try:
            normalize_question(QUESTION.replace('已送达订单', phrase))
        except NormalizationError:
            rejected += 1
        else:
            raise AssertionError('Unsupported clause was dropped')
    with tempfile.TemporaryDirectory(prefix='wording-') as temporary:
        catalog = accepted_catalog('/opt/ecommerce/data/processed/olist')
        executor = MetricExecutor(Path(temporary)/'audit.json', catalog=catalog)
        client = FixtureClient()
        result = invoke_v1_agent_graph({'run_id':'offline-wording-original', 'workspace_id':'ecommerce-v0',
            'snapshot_id':SNAPSHOT_ID, 'metric_version':'olist-delivered-purchase-item-price-v1',
            'node_id':'offline-wording-original', 'user_question':QUESTION,'trace':[]}, executor, client=client)
        assert result['status'] == 'success', result.get('error')
        assert client.calls == 3
        assert result['explanation']['grounded'] is True
        print(json.dumps({'status':'passed', 'fixture_model_calls':client.calls, 'real_model_calls':0,
            'unsupported_cases_rejected':rejected, 'conditions':conditions,
            'result':result['verified_result'], 'stages':[v['stage'] for v in result['trace']]}))


if __name__ == '__main__':
    main()
