"""Scoped adapter to the existing V1 business graph; model client is explicit."""
import hashlib
from pathlib import Path

from data_formulator.ecommerce.authorization import Principal, ResourceRef, authorize
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.execution_context import ExecutionContext
from data_formulator.ecommerce.executor import MetricExecutor
from data_formulator.ecommerce.v1_service import analyze_v1, validate_analyze_request
from data_formulator.ecommerce.workspace_state import lease


WORKSPACE = 'ecommerce-v0'


class ScopedWorkspace:
    def __init__(self, repository, principal, directory):
        self.repository, self.principal, self.directory = repository, principal, Path(directory)

    def read(self):
        return self.repository.read(self.principal.owner, WORKSPACE)

    def task_lease(self, request_id):
        scope = hashlib.sha256((self.principal.owner + '\0' + WORKSPACE).encode()).hexdigest()
        return lease(self.directory / scope / 'workspace.lease')

    def get_node(self, node_id):
        try:
            return self.repository.get_node(self.principal.owner, WORKSPACE, node_id)
        except ToolError as error:
            if error.code != 'NOT_FOUND':
                raise
            return None

    def parent_conditions(self, node_id):
        authorize(self.principal, 'parent', ResourceRef('node', WORKSPACE, node_id), self.repository)
        node = self.repository.get_node(self.principal.owner, WORKSPACE, node_id)
        if node['status'] == 'running':
            raise ToolError('BUSY', 'Parent is running')
        return node['conditions']

    def save_run(self, *, node_id, question, conditions, status, result=None, parent_node_id=None,
                 chart_spec=None, error=None):
        current = self.read()
        node = dict(node_id=node_id, question=question, conditions=conditions, status=status,
                    result=result or {}, parent_node_id=parent_node_id, chart_spec=chart_spec or {}, error=error or {})
        self.repository.save_node(self.principal.owner, WORKSPACE, node, expected_version=current['version'])
        return node


class MultiuserService:
    def __init__(self, repository, directory, client_factory):
        if not callable(client_factory):
            raise ValueError('An explicit governed model client is required')
        self.repository, self.directory, self.client_factory = repository, Path(directory), client_factory

    def workspace(self, principal):
        if not isinstance(principal, Principal):
            raise ToolError('AUTH_REQUIRED', 'Please log in')
        self.repository.create_workspace(principal.owner, WORKSPACE)
        authorize(principal, 'read', ResourceRef('workspace', WORKSPACE, WORKSPACE), self.repository)
        return ScopedWorkspace(self.repository, principal, self.directory)

    def analyze(self, principal, body, *, workspace_override=None, client_override=None, checkpoint=None):
        validate_analyze_request(body)
        workspace = workspace_override if workspace_override is not None else self.workspace(principal)
        if body.get('parent_node_id'):
            workspace.parent_conditions(body['parent_node_id'])
        executor = MetricExecutor(self.directory / 'execution-audit.json',
                                  context=ExecutionContext(principal, WORKSPACE, self.repository, checkpoint))
        client = client_override if client_override is not None else self.client_factory(principal, body['request_id'])
        if client is None:
            raise ToolError('MODEL_DISABLED', 'Explicit model client required')
        return analyze_v1(body, principal.owner, self.directory, workspace, executor=executor, client=client,
                          checkpoint=checkpoint)
