"""Object authorization using server-side metadata before reading result payloads."""
from dataclasses import dataclass

from data_formulator.ecommerce.contracts import ToolError


@dataclass(frozen=True)
class Principal:
    owner: str


@dataclass(frozen=True)
class ResourceRef:
    kind: str
    workspace: str
    id: str


ACTIONS = {
    'workspace': {'read', 'create', 'list'},
    'node': {'read', 'create', 'parent', 'replay'},
    'task': {'read', 'cancel', 'replay'},
    'snapshot': {'read'},
}


def authorize(principal, action, ref, repository):
    """principal must originate from the verified server session, never JSON."""
    if not isinstance(principal, Principal) or not principal.owner:
        raise ToolError('AUTH_REQUIRED', 'Please log in')
    if not isinstance(ref, ResourceRef) or action not in ACTIONS.get(ref.kind, set()):
        raise ToolError('NOT_FOUND', 'Resource not found')
    resource = repository.resource(principal.owner, ref)
    if resource is None:
        raise ToolError('NOT_FOUND', 'Resource not found')
    if ref.kind == 'snapshot':
        if not resource.get('approved_readonly') or action != 'read':
            raise ToolError('NOT_FOUND', 'Resource not found')
    elif resource.get('owner') != principal.owner or resource.get('workspace') != ref.workspace:
        raise ToolError('NOT_FOUND', 'Resource not found')
    return resource


def authorized_action(principal, action, ref, repository, callback):
    authorize(principal, action, ref, repository)
    return callback()
