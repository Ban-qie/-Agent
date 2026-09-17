"""Final V0-7 checks, including the browser bundle and inherited usage ledger."""
import json
from devtools.run_local import ROOT
from devtools.v06_verify import main

if __name__ == '__main__':
    main('V0-7', include_dist=True)
    old = json.loads((ROOT / 'docs/verification/V0-6-usage.json').read_text(encoding='utf-8'))
    rows = json.loads((ROOT / '.local/verification/qwen-usage.json').read_text(encoding='utf-8'))
    assert rows[:len(old)] == old
    print(json.dumps({'total_attempts': len(rows), 'new_attempts': len(rows) - len(old),
                      'input_tokens': sum(r.get('input_tokens', 0) for r in rows),
                      'output_tokens': sum(r.get('output_tokens', 0) for r in rows),
                      'estimated_cny': sum(r.get('estimated_cny', 0) for r in rows),
                      'reserved_cny': sum(r['reserved_cny'] for r in rows)}))
