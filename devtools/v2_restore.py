"""Isolated offline HTTP restart evidence; never runs an old campaign."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from devtools.run_local import configure_offline, ROOT


def serve(directory):
    configure_offline()
    os.environ['DATA_FORMULATOR_HOME'] = str(directory / 'runtime')
    os.environ['ECOMMERCE_ANALYSIS_ORCHESTRATOR'] = 'v1'
    from data_formulator.app import app
    from data_formulator.auth import identity
    from data_formulator.routes import ecommerce
    from data_formulator.datalake.workspace_manager import WorkspaceManager
    from data_formulator.ecommerce.v1_workspace import V1WorkspaceStore
    identity._provider, identity._localhost_identity = None, 'local:v2-restore'
    store = V1WorkspaceStore(WorkspaceManager(directory / 'runtime' / 'workspaces'), 'local:v2-restore')
    ecommerce._v1_workspace = lambda _: store
    @app.before_request
    def no_analysis():
        from flask import request
        if request.method == 'POST' and request.path.endswith('/analyze'):
            with (directory / 'unexpected-posts.log').open('a') as stream:
                stream.write('POST\n')
            return {'state': 'failed', 'error': {'code': 'MODEL_DISABLED'}}, 403
    app.run(host='127.0.0.1', port=5567, use_reloader=False)


def main():
    directory = ROOT / 'docs/verification/V2-S05' / sys.argv[1]
    if len(sys.argv) > 2 and sys.argv[2] == '--serve':
        serve(directory)
        return
    import socket
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', 5567)) == 0:
            raise RuntimeError('Port 5567 is occupied; do not stop the existing service')
    directory.mkdir(exist_ok=False)
    configure_offline()
    os.environ['DATA_FORMULATOR_HOME'] = str(directory / 'runtime')
    from data_formulator.datalake.workspace_manager import WorkspaceManager
    from data_formulator.ecommerce.v1_workspace import V1WorkspaceStore
    store = V1WorkspaceStore(WorkspaceManager(directory / 'runtime' / 'workspaces'), 'local:v2-restore')
    cases = [json.loads(p.read_text(encoding='utf-8')) for p in (ROOT / 'docs/verification/V1-R04/cases').glob('*.result.json')]
    done = set()
    while cases:
        available = [c for c in cases if not c['body'].get('parent_node_id') or c['body']['parent_node_id'] in done]
        if not available:
            raise RuntimeError('Missing historical parent fixture')
        for case in available:
            body, response = case['body'], case['response']
            store.save_run(node_id=body['request_id'], parent_node_id=body.get('parent_node_id'),
                question=body['user_question'], conditions=response.get('conditions') or {},
                status='waiting_clarification' if response['state'] == 'clarification_required' else response['state'],
                result=response, chart_spec=response.get('chart_spec'), error=response.get('error'))
            done.add(body['request_id'])
            cases.remove(case)
    root = store.read()['nodes'][0]
    store.save_run(node_id='v2-restore-failed', question='离线失败样本', conditions=root['conditions'], status='failed',
        result={'state': 'failed', 'result': root['result']['result'], 'error': {'code': 'INVALID_AGENT_OUTPUT'}})
    expected = store.read()
    (directory / 'expected.json').write_text(json.dumps(expected, ensure_ascii=False), encoding='utf-8')
    before_file = hashlib.sha256(store.file.read_bytes()).hexdigest()
    ledger = ROOT / '.local/verification/qwen-usage.json'
    before_ledger = hashlib.sha256(ledger.read_bytes()).hexdigest()
    for phase in ('before-restart', 'after-restart'):
        with (directory / (phase + '-server.log')).open('x', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-m', 'devtools.v2_restore', sys.argv[1], '--serve'],
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            from data_formulator.ecommerce.process_limits import constrain_process
            close_job = constrain_process(process, memory_bytes=1024 * 1024 * 1024)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Offline service exited; see log')
                    try:
                        with urlopen('http://127.0.0.1:5567/api/ecommerce/workspace', timeout=1) as r:
                            actual = json.load(r)
                        break
                    except OSError:
                        time.sleep(.2)
                else:
                    raise RuntimeError('Offline service startup timeout')
                assert actual == expected
                if phase == 'after-restart':
                    browser_env = {**os.environ, 'NODE_PATH': str(Path.home() / 'AppData/Local/AgentProjectTools/browser/node_modules')}
                    with (directory / 'browser.log').open('xb') as browser_log:
                        result = subprocess.run(['node', 'devtools/v2_restore_browser.cjs', str(directory)],
                            stdout=browser_log, stderr=subprocess.STDOUT, timeout=90, env=browser_env)
                    assert result.returncode == 0, 'Browser failed; see browser.log'
            finally:
                process.terminate()
                close_job()
                process.wait(timeout=5)
    assert hashlib.sha256(store.file.read_bytes()).hexdigest() == before_file
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before_ledger
    assert not (directory / 'unexpected-posts.log').exists()
    (directory / 'summary.json').write_text(json.dumps({'passed': True, 'nodes': len(expected['nodes']),
        'analysis_posts': 0, 'new_usage': 0, 'workspace_hash_unchanged': True, 'ledger_sha256': before_ledger}), encoding='utf-8')
    print('Restart and browser restoration passed')


if __name__ == '__main__':
    main()
