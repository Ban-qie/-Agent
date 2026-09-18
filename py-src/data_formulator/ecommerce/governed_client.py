"""Reserve before dispatch and retain unknown charges; no hidden retries."""
import json
import time

from data_formulator.ecommerce.contracts import ToolError


class GovernedClient:
    def __init__(self, client, budget, task, store):
        from data_formulator.ecommerce.budget import BudgetClient
        if isinstance(client, BudgetClient):
            raise ValueError('Legacy file-ledger client cannot write alongside the new ledger')
        self.client, self.budget, self.task, self.store = client, budget, task, store
        self.calls = 0
        self.deadline = time.monotonic() + 60

    def checkpoint(self):
        self.store.check_lease(self.task)
        if time.monotonic() >= self.deadline:
            raise ToolError('ANALYSIS_TIMEOUT', 'Task deadline exceeded')

    def get_completion(self, messages, **kwargs):
        self.checkpoint()
        if self.budget is None:
            raise ToolError('MODEL_DISABLED', 'Atomic model budget is required')
        if self.calls >= 3 or time.monotonic() >= self.deadline:
            raise ToolError('ANALYSIS_TIMEOUT', 'Task call or time limit reached')
        if len(json.dumps(messages, ensure_ascii=False).encode()) > 16384:
            raise ToolError('TOKEN_LIMIT', 'Input limit exceeded')
        self.calls += 1
        reservation = self.budget.reserve(self.task, self.calls)
        self.client.deadline = self.deadline
        self.client.checkpoint = self.checkpoint
        try:
            response = self.client.get_completion(messages, **kwargs)
        except BaseException:
            self.budget.finish(reservation)
            raise
        usage = getattr(response, 'usage', None)
        self.budget.finish(reservation, {'input_tokens': usage.prompt_tokens, 'output_tokens': usage.completion_tokens}
                           if usage is not None else None)
        self.store.check_lease(self.task)
        if time.monotonic() >= self.deadline:
            raise ToolError('ANALYSIS_TIMEOUT', 'Task deadline exceeded')
        return response
