"""Offline focused checks with exclusive, attempt-separated evidence files."""
import json
import sys
import uuid
from contextlib import redirect_stdout, redirect_stderr
from devtools.run_local import configure_offline, ROOT


def main():
    stage, attempt, *files = sys.argv[1:]
    directory = ROOT / 'docs/verification' / stage
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / f'{attempt}.log'
    report = directory / f'{attempt}.json'
    if log.exists() or report.exists():
        raise RuntimeError('Evidence already exists; choose a new attempt')
    configure_offline()
    import pytest
    records = []
    class Evidence:
        def pytest_runtest_logreport(self, report):
            if report.when == 'call' or report.failed:
                records.append({'case_id': report.nodeid, 'phase': report.when, 'outcome': report.outcome})
    with log.open('x', encoding='utf-8') as stream, redirect_stdout(stream), redirect_stderr(stream):
        status = pytest.main(files + ['-q', '-p', 'no:cacheprovider', '--basetemp',
            str(ROOT / '.local' / ('pytest-v2-' + uuid.uuid4().hex))], plugins=[Evidence()])
    result = {'exit_code': int(status), 'cases': records,
              'passed': sum(r['outcome'] == 'passed' for r in records),
              'failed': sum(r['outcome'] == 'failed' for r in records)}
    report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
