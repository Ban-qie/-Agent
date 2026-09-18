"""Conservative ledger migration and transactional per-dispatch reservations."""
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import uuid

from data_formulator.ecommerce.budget import UsageLedger, exclusive
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.workspace_repository import pack

UNIT = Decimal(1000000000)
PROJECT_CAP = 10000000000
RESERVATION = 20000000


def money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError('Invalid monetary amount')
    return int((amount * UNIT).to_integral_value(rounding=ROUND_CEILING))


class AtomicBudget:
    def __init__(self, store, source):
        self.store, self.source = store, Path(source).resolve()

    def initialize(self):
        with self.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS ledger_config ('
                       'id INTEGER PRIMARY KEY CHECK(id=1),source_hash TEXT NOT NULL,token TEXT NOT NULL,'
                       'legacy_spent INTEGER NOT NULL,live_cap INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS legacy_usage ('
                       'attempt INTEGER PRIMARY KEY,payload TEXT NOT NULL,spent INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS user_budget ('
                       'owner TEXT PRIMARY KEY REFERENCES accounts(id),cap INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS reservations ('
                       'id TEXT PRIMARY KEY,owner TEXT NOT NULL,workspace TEXT NOT NULL,task TEXT NOT NULL REFERENCES tasks(id),'
                       'dispatch INTEGER NOT NULL,reserved INTEGER NOT NULL,estimated INTEGER NOT NULL DEFAULT 0,'
                       'settlement TEXT,UNIQUE(task,dispatch))')

    def migrate_copy(self, *, live_cap_cny=0):
        cap = money(live_cap_cny)
        if cap > PROJECT_CAP:
            raise ValueError('Project cap cannot be expanded')
        self.initialize()
        with exclusive(self.source):
            marker = self.source.with_suffix('.migrated')
            if marker.exists():
                raise ValueError('Migration marker exists; do not overwrite')
            raw = self.source.read_bytes()
            rows = UsageLedger(self.source).read()
            with self.source.with_suffix('.migration-backup.json').open('xb') as handle:
                handle.write(raw)
            fingerprint, token = hashlib.sha256(raw).hexdigest(), uuid.uuid4().hex
            amounts = [max(money(r['reserved_cny']), money(r.get('estimated_cny', 0))) for r in rows]
            with self.store.transaction() as db:
                db.execute('INSERT INTO ledger_config VALUES(1,?,?,?,?)', (fingerprint, token, sum(amounts), cap))
                db.executemany('INSERT INTO legacy_usage VALUES(?,?,?)',
                               [(r['attempt'], pack(r), value) for r, value in zip(rows, amounts)])
            with marker.open('x', encoding='utf-8') as handle:
                json.dump({'database': str(self.store.path.resolve()), 'source_hash': fingerprint, 'token': token}, handle)
        return {'rows': len(rows), 'source_hash': fingerprint, 'legacy_spent': sum(amounts)}

    def _config(self, db):
        row = db.execute('SELECT * FROM ledger_config WHERE id=1').fetchone()
        try:
            marker = json.loads(self.source.with_suffix('.migrated').read_text(encoding='utf-8'))
            if (row is None or marker != {'database': str(self.store.path.resolve()),
                                         'source_hash': row['source_hash'], 'token': row['token']}
                    or hashlib.sha256(self.source.read_bytes()).hexdigest() != row['source_hash']):
                raise ValueError()
        except (ValueError, OSError):
            raise ToolError('BUDGET_UNAVAILABLE', 'Ledger migration requires review') from None
        return row

    def set_user_cap(self, owner, cap_cny):
        amount = money(cap_cny)
        if amount > PROJECT_CAP:
            raise ValueError('Project cap cannot be expanded')
        with self.store.transaction() as db:
            db.execute('INSERT INTO user_budget VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET cap=excluded.cap', (owner, amount))

    def reserve(self, task, dispatch):
        if type(dispatch) is not int or not 1 <= dispatch <= 3:
            raise ToolError('CALL_LIMIT', 'Three calls per task')
        with self.store.transaction() as db:
            config = self._config(db)
            current = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
            if (current is None or current['owner'] != task['owner'] or current['workspace'] != task['workspace']
                    or current['status'] != 'running' or current['version'] != task['version']
                    or current['lease_token'] != task['lease_token'] or current['lease_expiry'] <= self.store.clock()
                    or current['cancel_requested']):
                raise ToolError('INTERRUPTED', 'Task no longer owns dispatch')
            previous = db.execute('SELECT id FROM reservations WHERE task=? AND dispatch=?', (task['id'], dispatch)).fetchone()
            if previous:
                # Do not dispatch again even if the earlier reservation has no known result.
                raise ToolError('INTERRUPTED', 'Dispatch already reserved; no automatic replay')
            user = db.execute('SELECT cap FROM user_budget WHERE owner=?', (task['owner'],)).fetchone()
            global_spent = db.execute('SELECT coalesce(sum(max(reserved,estimated)),0) FROM reservations').fetchone()[0]
            user_spent = db.execute('SELECT coalesce(sum(max(reserved,estimated)),0) FROM reservations WHERE owner=?',
                                    (task['owner'],)).fetchone()[0]
            if (not user or user_spent + RESERVATION > user['cap']
                    or global_spent + RESERVATION > config['live_cap']
                    or config['legacy_spent'] + global_spent + RESERVATION > PROJECT_CAP):
                raise ToolError('BUDGET_EXHAUSTED', 'Model budget exhausted')
            reservation = uuid.uuid4().hex
            db.execute('INSERT INTO reservations(id,owner,workspace,task,dispatch,reserved) VALUES(?,?,?,?,?,?)',
                       (reservation, task['owner'], task['workspace'], task['id'], dispatch, RESERVATION))
        return reservation

    def finish(self, reservation, usage=None):
        if usage is not None and (set(usage) != {'input_tokens', 'output_tokens'} or
                                 any(type(v) is not int or v < 0 for v in usage.values())):
            raise ValueError('Invalid usage')
        settlement = pack(usage or {'usage': 'unknown'})
        estimated = money((Decimal(usage['input_tokens']) * Decimal('.15') +
                           Decimal(usage['output_tokens']) * Decimal('1.5')) / Decimal(1000000)) if usage else 0
        with self.store.transaction() as db:
            row = db.execute('SELECT settlement FROM reservations WHERE id=?', (reservation,)).fetchone()
            if row is None:
                raise ToolError('BUDGET_UNAVAILABLE', 'Unknown reservation')
            if row['settlement']:
                if row['settlement'] != settlement:
                    raise ToolError('REQUEST_CONFLICT', 'Reservation already settled')
                return
            db.execute('UPDATE reservations SET settlement=?,estimated=? WHERE id=?', (settlement, estimated, reservation))

    def totals(self):
        with self.store.transaction() as db:
            config = self._config(db)
            new = db.execute('SELECT coalesce(sum(max(reserved,estimated)),0) FROM reservations').fetchone()[0]
            return {'legacy': config['legacy_spent'], 'new': new, 'total': config['legacy_spent'] + new}
