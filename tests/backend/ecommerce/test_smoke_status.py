from devtools.v4_smoke_status import describe


def test_clarification_is_distinct_from_login_failure():
    lines = describe({'submit_http_status': 202, 'status': 'failed',
                      'response': {'state': 'clarification_required', 'executed': False,
                                   'budget': {'model_calls': 1}}})
    assert lines[0].startswith('登录：成功')
    assert '不是登录失败' in lines[2]
    assert '审计为准' in lines[1]


def test_missing_submission_does_not_claim_login_or_model_success():
    lines = describe({'status': 'failed', 'submit_attempts': 0})
    assert '不足以确认' in lines[0]
    assert '不足以确认' in lines[1]


def test_success_does_not_claim_numeric_acceptance():
    lines = describe({'submit_http_status': 202,
                      'response': {'state': 'success', 'executed': True}})
    assert '另行核对' in lines[2]
