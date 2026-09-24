"""Private bounded wire adapter. Never reads or writes the usage ledger."""
import contextlib
import json
import os
import sys


def error_category(error):
    name = type(error).__name__.lower()
    if 'timeout' in name:
        return 'timeout'
    if 'auth' in name or 'permission' in name:
        return 'authentication'
    if 'rate' in name:
        return 'rate_limit'
    if 'connect' in name or 'network' in name:
        return 'connection'
    return 'provider'


def main():
    wire = sys.stdout
    # Library diagnostics must never contaminate the wire or reveal credentials.
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            payload = json.loads(sys.stdin.buffer.read(128 * 1024))
            from data_formulator.agents.client_utils import Client
            from data_formulator.ecommerce.model_transport import MAX_RESPONSE_BYTES
            client = Client(payload['endpoint'], payload['model'])
            response = client._dispatch(messages=payload['messages'], stream=payload['stream'],
                                        params=payload['params'], tools=payload['tools'])
            if payload['stream']:
                chunks, size = [], 0
                try:
                    for chunk in response:
                        item = chunk.model_dump(mode='json')
                        size += len(json.dumps(item).encode())
                        if size > MAX_RESPONSE_BYTES - 4096:
                            raise ValueError('response limit')
                        chunks.append(item)
                finally:
                    if hasattr(response, 'close'):
                        response.close()
                output = {'chunks': chunks}
            else:
                output = {'response': response.model_dump(mode='json')}
            encoded = json.dumps(output)
            if len(encoded.encode()) > MAX_RESPONSE_BYTES:
                raise ValueError('response limit')
        except Exception as error:
            encoded = json.dumps({
                'error': 'MODEL_FAILED',
                'category': error_category(error),
            })
    wire.write(encoded)


if __name__ == '__main__':
    main()
