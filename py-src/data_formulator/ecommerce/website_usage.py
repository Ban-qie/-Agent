"""Website accounting only: no debugging campaign or monetary quota."""
from decimal import Decimal
import uuid
from data_formulator.ecommerce.atomic_budget import money, RESERVATION
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.workspace_repository import pack


class WebsiteUsage:
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS website_usage (
                id TEXT PRIMARY KEY,owner TEXT NOT NULL,workspace TEXT NOT NULL,
                task TEXT NOT NULL REFERENCES tasks(id),dispatch INTEGER NOT NULL,
                reserved INTEGER NOT NULL,estimated INTEGER NOT NULL DEFAULT 0,
                settlement TEXT,UNIQUE(task,dispatch))""")

    def reserve(self, task, dispatch):
        if type(dispatch) is not int or not 1 <= dispatch <= 3:
            raise ToolError('CALL_LIMIT', 'Three calls per task')
        with self.store.transaction() as db:
            current = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
            if (current is None or current['owner'] != task['owner'] or current['workspace'] != task['workspace']
                    or current['status'] != 'running' or current['version'] != task['version']
                    or current['lease_token'] != task['lease_token'] or current['lease_expiry'] <= self.store.clock()
                    or current['cancel_requested']):
                raise ToolError('INTERRUPTED', 'Task no longer owns dispatch')
            previous = db.execute('SELECT id FROM website_usage WHERE task=? AND dispatch=?', (task['id'], dispatch)).fetchone()
            if previous:
                # Do not dispatch again even if the earlier reservation has no known result.
                raise ToolError('INTERRUPTED', 'Dispatch already reserved; no automatic replay')
            reservation = uuid.uuid4().hex
            db.execute('INSERT INTO website_usage(id,owner,workspace,task,dispatch,reserved) VALUES(?,?,?,?,?,?)',
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
            row = db.execute('SELECT settlement FROM website_usage WHERE id=?', (reservation,)).fetchone()
            if row is None:
                raise ToolError('BUDGET_UNAVAILABLE', 'Unknown reservation')
            if row['settlement']:
                if row['settlement'] != settlement:
                    raise ToolError('REQUEST_CONFLICT', 'Reservation already settled')
                return
            db.execute('UPDATE website_usage SET settlement=?,estimated=? WHERE id=?', (settlement, estimated, reservation))

