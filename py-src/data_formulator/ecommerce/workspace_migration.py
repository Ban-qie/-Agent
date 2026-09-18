"""Offline copy-only V1 migration. Never activates over a live database."""
import hashlib
import os
from pathlib import Path

from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.v1_workspace import V1WorkspaceStore
from data_formulator.ecommerce.workspace_repository import WorkspaceRepository, pack


def migrate_v1(source, repository, owner, destination, *, before_activate=None):
    source, destination = Path(source), Path(destination)
    if not owner or destination.exists():
        raise ValueError('Explicit owner and unused destination required')
    original = source.read_bytes()
    fingerprint = hashlib.sha256(original).hexdigest()
    backup = destination.with_suffix('.source-backup.json')
    with backup.open('xb') as handle:
        handle.write(original)
    validator = object.__new__(V1WorkspaceStore)
    validator.file = source
    state = validator._read_unlocked()
    if any(n['status'] == 'running' for n in state['nodes']):
        raise ToolError('BUSY', 'Review running legacy nodes before migration')
    temporary = destination.with_suffix('.staging.sqlite')
    if temporary.exists():
        raise ValueError('Staging database already exists; retain for review')
    repository.backup(temporary)
    converted = WorkspaceRepository(temporary)
    with converted.transaction() as db:
        if not db.execute('SELECT 1 FROM accounts WHERE id=?', (owner,)).fetchone():
            raise ValueError('Explicit owner is not provisioned')
        db.execute('INSERT INTO workspaces(owner,id) VALUES(?,?)', (owner, state['workspace_id']))
        for version, node in enumerate(state['nodes']):
            payload = pack(node)
            converted._save_node(db, owner, state['workspace_id'], node, payload,
                                 len(payload.encode('utf-8')), version)
    actual = converted.read(owner, state['workspace_id'])
    if actual['nodes'] != state['nodes']:
        raise ValueError('Migration roundtrip mismatch')
    if before_activate:
        before_activate()
    if source.read_bytes() != original:
        raise ValueError('Legacy source changed during migration')
    # Hard-link creation is atomic and refuses an existing destination; no overwrite.
    os.link(temporary, destination)
    return {'source_sha256': fingerprint, 'nodes': len(state['nodes']),
            'owner': owner, 'new_model_calls': 0, 'destination': str(destination)}
