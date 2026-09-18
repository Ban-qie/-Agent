import hashlib
import json
from pathlib import Path

import pytest

from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.workspace_migration import migrate_v1
from data_formulator.ecommerce.workspace_repository import WorkspaceRepository


def setup(tmp_path):
    store = WorkspaceRepository(tmp_path / 'base.sqlite')
    store.initialize()
    owner = store.create('alice', 'test-password-A')
    return store, owner


def test_historical_twelve_nodes_copy_roundtrip(tmp_path):
    root = Path(__file__).resolve().parents[3]
    source = root / 'docs/verification/V2-S05/c-attempt4/expected.json'
    if not source.exists():
        pytest.skip('Private historical evidence unavailable in distributable checkout')
    original = source.read_bytes()
    ledger = root / '.local/verification/qwen-usage.json'
    ledger_hash = hashlib.sha256(ledger.read_bytes()).hexdigest()
    store, owner = setup(tmp_path)
    destination = tmp_path / 'imported.sqlite'
    receipt = migrate_v1(source, store, owner, destination)
    expected = json.loads(original)
    assert receipt['nodes'] == 12
    assert WorkspaceRepository(destination).read(owner, 'ecommerce-v0')['nodes'] == expected['nodes']
    assert source.read_bytes() == original
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == ledger_hash
    assert (tmp_path / 'imported.source-backup.json').read_bytes() == original


@pytest.mark.parametrize('failure', ['unknown-schema', 'missing-owner', 'before-activate'])
def test_failed_migration_preserves_source_and_does_not_activate(tmp_path, failure):
    store, owner = setup(tmp_path)
    source = tmp_path / 'legacy.json'
    source.write_text(json.dumps({'schema_version': 999 if failure == 'unknown-schema' else 1,
                                 'workspace_id': 'ecommerce-v0', 'nodes': []}), encoding='utf-8')
    original = source.read_bytes()
    destination = tmp_path / 'new.sqlite'
    def fail():
        raise OSError('simulated failure before activate')
    with pytest.raises((ToolError, ValueError, OSError)):
        migrate_v1(source, store, 'missing' if failure == 'missing-owner' else owner, destination,
                   before_activate=fail if failure == 'before-activate' else None)
    assert source.read_bytes() == original
    assert not destination.exists()
    assert store.principal(owner, 1)['id'] == owner
