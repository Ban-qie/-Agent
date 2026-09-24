"""Explain existing smoke evidence without logging in or submitting requests."""
import argparse
import json
from pathlib import Path


def describe(evidence):
    submitted = evidence.get('submit_http_status') == 202
    response = evidence.get('response') or {}
    state = response.get('state')
    if submitted:
        login = '登录：成功（已取得受保护分析接口的任务受理响应）'
    elif evidence.get('login_http_status') == 200:
        login = '登录：成功'
    else:
        login = '登录：当前证据不足以确认，请查看登录步骤错误'
    if evidence.get('provider_usage_verified') is True:
        model = '模型调用：已核对供应商返回的用量'
    elif state in {'success', 'clarification_required'} and (response.get('budget') or {}).get('model_calls', 0) > 0:
        model = '模型步骤：已结束；是否收到供应商响应请以服务端用量审计为准'
    else:
        model = '模型调用：当前客户端证据不足以确认成功'
    if state == 'success' and response.get('executed') is True:
        analysis = '分析：已返回结果；数值与账目验收另行核对'
    elif state == 'clarification_required':
        analysis = '分析验收：未通过，需要澄清，尚未执行数据查询（不是登录失败）'
    else:
        analysis = '分析验收：未通过或尚未完成，请查看已保存证据'
    return [login, model, analysis, '本命令仅读取结果，不重新登录、不提交分析、不调用模型。']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding='utf-8-sig'))
    print('\n'.join(describe(evidence)))


if __name__ == '__main__':
    main()
