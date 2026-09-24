"""Killable model transport; only the parent owns accounting and deadlines."""
import json
import logging
import subprocess
import sys
import time

from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.executor import worker_environment
from data_formulator.ecommerce.process_limits import constrain_process

MAX_RESPONSE_BYTES = 512 * 1024
CLEANUP_SECONDS = 2
ERROR_CATEGORIES = frozenset({'timeout', 'authentication', 'rate_limit',
                              'connection', 'provider'})
LOGGER = logging.getLogger(__name__)


def dispatch(client, *, messages, stream, params, tools=None):
    remaining = client.deadline - time.monotonic()
    if remaining <= 0:
        raise ToolError('ANALYSIS_TIMEOUT', 'Task deadline exceeded')
    payload = json.dumps({'endpoint': client.endpoint, 'model': client.model,
                          'messages': messages, 'stream': stream, 'params': params,
                          'tools': tools}, ensure_ascii=False).encode('utf-8')
    env = worker_environment()
    env['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    process = subprocess.Popen([sys.executable, '-I', '-m',
        'data_formulator.ecommerce.model_worker'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        start_new_session=sys.platform == 'linux',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    close_job = None
    try:
        close_job = constrain_process(process)
        # No request bytes (including credentials) are sent until limits exist.
        if getattr(client, 'checkpoint', None) is None:
            output, _ = process.communicate(payload, timeout=max(.001, client.deadline - time.monotonic()))
        else:
            from data_formulator.ecommerce.process_wait import communicate
            output, _ = communicate(process, payload, client.deadline, client.checkpoint)
        if time.monotonic() >= client.deadline:
            raise ToolError('ANALYSIS_TIMEOUT', 'Task deadline exceeded')
        if process.returncode or len(output) > MAX_RESPONSE_BYTES:
            raise ToolError('MODEL_FAILED', 'Model transport did not complete')
        data = json.loads(output)
        if data.get('error'):
            category = data.get('category')
            if category in ERROR_CATEGORIES:
                LOGGER.warning('Qwen model transport failed: category=%s', category)
            raise ToolError('MODEL_FAILED', 'Model transport did not complete')
        from litellm import ModelResponse
        from litellm.types.utils import ModelResponseStream
        if stream:
            return iter(ModelResponseStream(**chunk) for chunk in data['chunks'])
        return ModelResponse(**data['response'])
    except subprocess.TimeoutExpired:
        raise ToolError('ANALYSIS_TIMEOUT', 'Task deadline exceeded') from None
    finally:
        if process.poll() is None:
            process.kill()
        if close_job:
            close_job()  # KILL_ON_JOB_CLOSE includes the venv interpreter child.
        process.communicate(timeout=CLEANUP_SECONDS)
