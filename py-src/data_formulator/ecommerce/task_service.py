"""Bounded background execution of the existing graph, with durable idempotency."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import threading

from data_formulator.ecommerce.authorization import Principal
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.multiuser_service import ScopedWorkspace, WORKSPACE
from data_formulator.ecommerce.task_store import TERMINAL
from data_formulator.ecommerce.v1_service import validate_analyze_request


class DeferredWorkspace(ScopedWorkspace):
    """Do not persist intermediate graph state outside the task transaction."""
    pending_node = None

    def task_lease(self, _request_id):
        return nullcontext()

    def save_run(self, **values):
        if values['status'] != 'running':
            self.pending_node = {**values, 'result': values.get('result') or {},
                                 'chart_spec': values.get('chart_spec') or {}, 'error': values.get('error') or {}}
        return values


def public_task(task):
    value = {key: task[key] for key in ('request_id', 'status')}
    value['task_id'] = task['id']
    value['cancel_requested'] = bool(task['cancel_requested'])
    if task['response']:
        value['response'] = json.loads(task['response'])
    return value


class TaskService:
    def __init__(self, store, business, budget=None):
        self.store, self.business = store, business
        self.budget = budget
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='v3-task')
        self.slots = threading.BoundedSemaphore(2)
        self.futures = set()
        self.lock = threading.Lock()

    def workspace(self, principal):
        return self.business.workspace(principal)

    def submit(self, principal, body):
        validate_analyze_request(body)
        workspace = self.workspace(principal)
        if body.get('parent_node_id'):
            workspace.parent_conditions(body['parent_node_id'])
        self.store.recover_expired()
        task, created = self.store.create_or_get(principal.owner, WORKSPACE, body)
        if created:
            if not self.slots.acquire(blocking=False):
                self.store.request_cancel(principal.owner, WORKSPACE, task['id'])
                raise ToolError('BUSY', 'Execution slots busy')
            try:
                future = self.pool.submit(self._run, task['id'])
            except BaseException:
                self.slots.release()
                self.store.request_cancel(principal.owner, WORKSPACE, task['id'])
                raise
            with self.lock:
                self.futures.add(future)
            future.add_done_callback(self._done)
        return task

    def _done(self, future):
        with self.lock:
            self.futures.discard(future)

    def _run(self, task_id):
        task = None
        try:
            task = self.store.claim(task_id)
            principal = Principal(task['owner'])
            workspace = DeferredWorkspace(self.store, principal, self.business.directory)
            from data_formulator.ecommerce.governed_client import GovernedClient
            client = GovernedClient(self.business.client_factory(principal, task['request_id']),
                                    self.budget, task, self.store)
            result = self.business.analyze(principal, json.loads(task['body']), workspace_override=workspace,
                                           client_override=client, checkpoint=client.checkpoint)
            self.store.finish_if_owner_version(task, result, workspace.pending_node)
        except Exception as error:
            if task:
                try:
                    self.store.finish_if_owner_version(task, {'state': 'failed', 'error': {
                        'code': error.code if isinstance(error, ToolError) else 'TASK_FAILED',
                        'message': 'Task could not complete'}})
                except Exception:
                    # Durable running lease becomes interrupted, never re-executed.
                    pass
        finally:
            self.slots.release()

    def get(self, principal, task_id):
        return self.store.get_authorized(principal.owner, WORKSPACE, task_id)

    def cancel(self, principal, task_id):
        return self.store.request_cancel(principal.owner, WORKSPACE, task_id)

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=False)
