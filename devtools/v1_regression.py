"""Run the related full backend suite and write V1-15 evidence without overwriting V0."""
import json
import time
import uuid
from devtools.run_local import configure_offline, ROOT


def main():
    configure_offline()
    import pytest
    class Evidence:
        def __init__(self):
            self.tests = []
        def pytest_runtest_logreport(self, report):
            if report.when == 'call' or report.failed:
                self.tests.append({'test': report.nodeid, 'phase': report.when,
                                   'outcome': report.outcome, 'seconds': round(report.duration, 4)})
    evidence = Evidence()
    start = time.monotonic()
    status = pytest.main(['tests/backend/ecommerce', 'tests/backend/agents/test_qwen_probe.py',
        'tests/backend/auth/test_auth.py', 'tests/backend/auth/test_local_username.py',
        'tests/backend/routes/test_list_global_models_api.py', 'tests/backend/data/test_workspace_manager.py',
        'tests/backend/agents/test_analyst_scratch_files.py', '-q', '-p', 'no:cacheprovider',
        '--basetemp', str(ROOT / '.local' / ('pytest-' + uuid.uuid4().hex))], plugins=[evidence])
    report = {'stage': 'V1-15', 'exit_code': int(status), 'seconds': round(time.monotonic() - start, 3),
              'passed': sum(t['outcome'] == 'passed' for t in evidence.tests),
              'failed': sum(t['outcome'] == 'failed' for t in evidence.tests), 'tests': evidence.tests}
    (ROOT / 'docs/verification/V1-15-regression.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
