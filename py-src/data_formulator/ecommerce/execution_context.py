"""Server-owned execution capability, checked against scoped repository metadata."""
from dataclasses import dataclass

from data_formulator.ecommerce.authorization import Principal, ResourceRef, authorize
from data_formulator.ecommerce.contracts import ToolError


@dataclass(frozen=True)
class ExecutionContext:
    principal: Principal
    workspace: str
    repository: object
    checkpoint: object = None

    def check(self, identity, request):
        if self.checkpoint:
            self.checkpoint()
        if identity != self.principal.owner:
            raise ToolError('ACCESS_DENIED', 'Execution identity mismatch')
        authorize(self.principal, 'read', ResourceRef('workspace', self.workspace, self.workspace), self.repository)
        authorize(self.principal, 'read', ResourceRef('snapshot', '', request.snapshot_id), self.repository)
        return self.principal.owner + '\0' + self.workspace
