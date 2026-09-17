"""Read-only V0-8 integrity checks, including persisted workspace state."""
import json
from devtools.run_local import ROOT
from devtools.qwen_config import read_user_key
from devtools.v06_verify import main


if __name__ == '__main__':
    main('V0-8', include_dist=True)
    old = json.loads((ROOT / 'docs/verification/V0-7-usage.json').read_text(encoding='utf-8'))
    rows = json.loads((ROOT / '.local/verification/qwen-usage.json').read_text(encoding='utf-8'))
    assert rows == old
    key = read_user_key().encode()
    files = list((ROOT / '.local/runtime/users').rglob('session_state.json'))
    assert files and not any(key in p.read_bytes() for p in files)
    report = {'workspace_files_scanned': len(files), 'workspace_key_matches': 0,
              'ledger_equals_v07': True, 'new_model_attempts': 0,
              'total_attempts': len(rows), 'reserved_cny': sum(r['reserved_cny'] for r in rows),
              'estimated_cny': sum(r.get('estimated_cny', 0) for r in rows)}
    (ROOT / 'docs/verification/V0-8-workspace-integrity.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
