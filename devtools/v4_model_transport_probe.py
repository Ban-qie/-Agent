"""Exercise the real SDK/worker against loopback fixtures in a network-none image.

Use the production image with --network none --memory 1g --pids-limit 64,
--read-only and the production /tmp tmpfs. No credentials or database needed.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import time
from types import SimpleNamespace

os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'

from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.model_transport import dispatch


def main():
    seen = []
    fixture = {'mode': 'success'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(request)
            mode = fixture['mode']
            status = mode if isinstance(mode, int) else 200
            if status != 200:
                data = {'error': {'message': 'offline fixture', 'type': 'fixture', 'code': 'fixture'}}
            else:
                data = {'id': 'offline-fixture', 'object': 'chat.completion', 'created': 1,
                        'model': 'qwen-flash', 'choices': [{'index': 0, 'finish_reason': 'stop',
                        'message': {'role': 'assistant', 'content': 'x' * 600000 if mode == 'large' else '{}'}}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        for mode in ('success', 401, 429, 503, 'large', 'success'):
            fixture['mode'] = mode
            before = len(seen)
            client = SimpleNamespace(endpoint='openai', model='qwen-flash', deadline=time.monotonic() + 30)
            started = time.monotonic()
            try:
                response = dispatch(client, messages=[{'role': 'user', 'content': 'Return JSON.'}],
                                    stream=False, params={'api_key': 'fake-offline-key',
                                    'api_base': f'http://127.0.0.1:{server.server_port}/v1',
                                    'timeout': 15, 'num_retries': 0, 'max_retries': 0,
                                    'max_tokens': 768, 'temperature': 0, 'enable_thinking': False,
                                    'response_format': {'type': 'json_object'}})
            except ToolError as error:
                assert mode != 'success', f'Valid HTTP 200 rejected: {error.code}'
                assert error.code == 'MODEL_FAILED'
            else:
                assert mode == 'success', 'Invalid/oversized response was accepted'
                assert response.choices[0].message.content == '{}'
                assert response.usage.prompt_tokens == 10
                assert response.usage.completion_tokens == 5
            assert len(seen) - before == 1, 'Unexpected SDK retry or missing request'
            assert seen[-1]['enable_thinking'] is False
            assert seen[-1]['max_tokens'] == 768
            assert seen[-1]['response_format'] == {'type': 'json_object'}
            results.append({'case': mode, 'passed': True, 'fixture_requests': 1,
                            'seconds': round(time.monotonic() - started, 3)})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    print(json.dumps({'passed': results, 'real_model_requests': 0, 'ledger_writes': 0}))


if __name__ == '__main__':
    main()
