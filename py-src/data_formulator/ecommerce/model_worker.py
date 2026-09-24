"""Private bounded wire adapter. Never reads or writes the usage ledger."""
import contextlib
import json
import os
import sys


SAFE_PROVIDER_CODES = frozenset({
    'Arrearage',
    'InvalidApiKey',
    'InvalidParameter',
    'ModelNotFound',
    'PermissionDenied',
    'QuotaExceeded',
    'RateLimitExceeded',
    'Throttling',
    'UnsupportedOperation',
})


def _safe_status(error):
    status = getattr(error, 'status_code', None)
    if not isinstance(status, int):
        response = getattr(error, 'response', None)
        status = getattr(response, 'status_code', None)
    return status if isinstance(status, int) and 400 <= status <= 599 else None


def _safe_provider_code(error):
    candidates = [getattr(error, 'code', None)]
    body = getattr(error, 'body', None)
    if isinstance(body, dict):
        candidates.append(body.get('code'))
        nested = body.get('error')
        if isinstance(nested, dict):
            candidates.append(nested.get('code'))
    for value in candidates:
        if isinstance(value, str) and value in SAFE_PROVIDER_CODES:
            return value
    return None


def error_diagnostic(error):
    name = type(error).__name__.lower()
    status = _safe_status(error)
    code = _safe_provider_code(error)
    rate_type = getattr(error, 'rate_limit_type', None)
    if 'timeout' in name:
        category = 'timeout'
    elif 'responsevalidation' in name or 'jsonschema' in name or 'decode' in name:
        category = 'response_parse'
    elif 'auth' in name or status == 401 or code == 'InvalidApiKey':
        category = 'authentication'
    elif 'permission' in name or status == 403 or code == 'PermissionDenied':
        category = 'permission'
    elif (rate_type == 'budget' or code in {'Arrearage', 'QuotaExceeded'}
          or 'budget' in name or 'quota' in name):
        category = 'quota'
    elif 'rate' in name or status == 429 or code in {'RateLimitExceeded', 'Throttling'}:
        category = 'rate_limit'
    elif 'connect' in name or 'network' in name:
        category = 'connection'
    elif status is not None and status >= 500:
        category = 'server'
    elif 'badrequest' in name or 'unprocessable' in name or status in {400, 404, 409, 422}:
        category = 'bad_request'
    else:
        category = 'provider'
    diagnostic = {'category': category}
    if status is not None:
        diagnostic['status'] = status
    if code is not None:
        diagnostic['provider_code'] = code
    return diagnostic


def main():
    wire = sys.stdout
    # Library diagnostics must never contaminate the wire or reveal credentials.
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            payload = json.loads(sys.stdin.buffer.read(128 * 1024))
            from data_formulator.agents.client_utils import Client
            from data_formulator.ecommerce.model_wire import MAX_RESPONSE_BYTES
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
            encoded = json.dumps({'error': 'MODEL_FAILED', **error_diagnostic(error)})
    wire.write(encoded)


if __name__ == '__main__':
    main()
