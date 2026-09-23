import json
from pathlib import Path

import pytest

from devtools.restore_ecommerce import load_verified_manifest
from devtools.v4_backup_common import confined, sha256_file


def write_backup(root):
    root.mkdir()
    (root / 'database.dump').write_bytes(b'database-archive')
    snapshot = root / 'snapshot'
    snapshot.mkdir()
    (snapshot / 'snapshot.json').write_text('{}', encoding='utf-8')
    manifest = {
        'format_version': 1, 'credentials_included': False,
        'database': {'file': 'database.dump', 'bytes': 16,
                     'sha256': sha256_file(root / 'database.dump')},
        'snapshot': {'directory': 'snapshot', 'files': {
            'snapshot.json': {'bytes': 2, 'sha256': sha256_file(snapshot / 'snapshot.json')},
        }},
    }
    path = root / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    (root / 'manifest.sha256').write_text(sha256_file(path) + '  manifest.json\n', encoding='ascii')
    return root


def test_paths_must_be_inside_designated_root(tmp_path):
    allowed = tmp_path / 'allowed'
    allowed.mkdir()
    assert confined(allowed / 'child', allowed) == (allowed / 'child').resolve()
    with pytest.raises(ValueError):
        confined(tmp_path / 'outside', allowed)
    with pytest.raises(ValueError):
        confined(allowed, allowed)


def test_manifest_archive_and_snapshot_are_verified(tmp_path):
    backup = write_backup(tmp_path / 'backup')
    assert load_verified_manifest(backup)['format_version'] == 1
    (backup / 'snapshot/snapshot.json').write_text('{"changed":true}', encoding='utf-8')
    with pytest.raises(ValueError, match='inventory'):
        load_verified_manifest(backup)


def test_missing_or_changed_manifest_is_rejected(tmp_path):
    backup = write_backup(tmp_path / 'backup')
    (backup / 'manifest.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='checksum'):
        load_verified_manifest(backup)
    (backup / 'manifest.json').unlink()
    with pytest.raises(ValueError, match='missing'):
        load_verified_manifest(backup)
