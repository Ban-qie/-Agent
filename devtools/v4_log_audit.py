"""V4-S02d secret redaction and actual Docker log-bound audit."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'py-src'))

from devtools.service_resilience import ORIGIN, ResilienceFixture, file_hash, login, wait_task


def next_output():
    directory = ROOT / 'docs/verification/V4-S02'
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for path in directory.glob('logs-attempt*.json'):
        try:
            attempts.append(int(path.stem.rsplit('attempt', 1)[1]))
        except (IndexError, ValueError):
            pass
    return directory / f'logs-attempt{max(attempts, default=0) + 1}.json'


def docker_log_config(container):
    process = subprocess.run(
        ['docker', 'inspect', '--format', '{{json .HostConfig.LogConfig}}', container],
        cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
    if process.returncode:
        raise RuntimeError('Docker log configuration is unavailable')
    return json.loads(process.stdout)


def main():
    output = next_output()
    ledger = ROOT / '.local/verification/qwen-usage.json'
    ledger_before = file_hash(ledger)
    markers = [
        'Bearer SYNTHETIC-AUTHORIZATION-92f4',
        'v3_session=SYNTHETIC-COOKIE-a13c',
        'postgresql://user:SYNTHETIC-DSN-f88d@host/db',
        'SYNTHETIC-TOKEN-743e',
    ]
    fixture = ResilienceFixture()
    started = time.time()
    result = {}
    try:
        with fixture:
            original = fixture.business.analyze
            def fail(*_args, **_kwargs):
                raise RuntimeError(' | '.join(markers))
            fixture.business.analyze = fail
            client = fixture.app.test_client()
            csrf = login(client, 'alice', 'fixture-password-A')
            response = client.post('/api/ecommerce/analyze', base_url=ORIGIN,
                                   headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf,
                                            'Authorization': markers[0],
                                            'X-Synthetic-Token': markers[3],
                                            'Cookie': markers[1]},
                                   json={'request_id': 'log-audit-failure-001',
                                         'user_question': 'synthetic exception path'})
            if response.status_code != 202:
                raise AssertionError(response.get_json())
            task = wait_task(client, response.get_json()['task_id'])
            if task['status'] != 'failed':
                raise AssertionError(task)
            fixture.business.analyze = original

            payloads = [json.dumps(task, ensure_ascii=False)]
            for root in (fixture.identity.runtime_root, fixture.identity.audit_root):
                for path in root.rglob('*'):
                    if path.is_file() and path.stat().st_size <= 10 * 1024 * 1024:
                        payloads.append(path.read_text(encoding='utf-8', errors='replace'))
            matches = [marker for marker in markers if any(marker in payload for payload in payloads)]
            if matches:
                raise AssertionError('Synthetic secret reached persisted output')

            containers = {
                name: docker_log_config('agent-ecommerce-v4-' + name + '-1')
                for name in ('postgres', 'redis', 'app', 'caddy')
            }
            expected = {'Type': 'json-file', 'Config': {'max-file': '3', 'max-size': '10m'}}
            if any(config != expected for config in containers.values()):
                raise AssertionError(containers)
            result = {
                'status': 'passed', 'step': 'V4-S02d',
                'utc': datetime.now(timezone.utc).isoformat(),
                'synthetic_secret_count': len(markers), 'secret_matches': matches,
                'unexpected_500': 0, 'task_status': task['status'],
                'docker_log_config': containers,
                'bounded_log_files_per_container': 3,
                'bounded_log_size_per_file': '10m',
                'qwen_calls': 0, 'qwen_ledger_sha256_before': ledger_before,
            }
    except Exception as error:
        result = {'status': 'failed', 'step': 'V4-S02d',
                  'error_type': type(error).__name__, 'qwen_calls': 0,
                  'qwen_ledger_sha256_before': ledger_before}
        raise
    finally:
        result['cleanup'] = fixture.cleanup
        result['qwen_ledger_sha256_after'] = file_hash(ledger)
        result['ledger_unchanged'] = result['qwen_ledger_sha256_after'] == ledger_before
        result['duration_seconds'] = round(time.time() - started, 3)
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(output), **result}, indent=2))


if __name__ == '__main__':
    main()
