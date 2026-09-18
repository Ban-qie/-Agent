"""V1 Qwen transport with V3-owned accounting and no implicit retries."""
import time

from data_formulator.agents.client_utils import Client
from data_formulator.ecommerce.contracts import ToolError


class QwenClient(Client):
    deadline = 0

    def ping(self, timeout=10):
        raise ToolError('TOOL_NOT_ALLOWED', 'Implicit model calls are disabled')

    def get_completion(self, messages, stream=False, **kwargs):
        # Bypass Client's fallback retries: each governed dispatch is exactly once.
        if stream:
            raise ToolError('TOOL_NOT_ALLOWED', 'The analysis graph uses buffered responses')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError('ANALYSIS_TIMEOUT', 'Task deadline exceeded')
        from data_formulator.ecommerce.model_transport import dispatch
        options = {**self.params, **kwargs}
        options.pop('reasoning_effort', None)
        options.update(max_tokens=768, timeout=min(10, remaining), num_retries=0,
                       max_retries=0, temperature=0, enable_thinking=False)
        return dispatch(self, messages=messages, stream=False, params=options)

    def get_completion_with_tools(self, *args, **kwargs):
        raise ToolError('TOOL_NOT_ALLOWED', 'Only the fixed analysis graph is enabled')


def configured_qwen(principal, request_id):
    from data_formulator.ecommerce.budget import configured_client
    return configured_client('website:' + principal.owner + ':' + request_id, QwenClient)
