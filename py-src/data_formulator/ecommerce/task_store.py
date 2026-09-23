"""Task state and fenced transaction ownership; no automatic paid replay."""
import hashlib
import json
import uuid

from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.workspace_repository import WorkspaceRepository, pack

TERMINAL = frozenset({'success', 'empty_result', 'waiting_clarification', 'failed', 'cancelled', 'interrupted'})


def terminal_status(response):
    status = response.get('state')
    mapped = {'partial': 'failed', 'clarification_required': 'waiting_clarification'}.get(status, status)
    if mapped not in TERMINAL:
        raise ToolError('INVALID_STATE', 'Unknown task result state')
    return mapped


def transition(current, target):
    if current in TERMINAL or not ((current == 'accepted' and target in {'running', 'cancelled', 'interrupted'})
                                  or (current == 'running' and target in TERMINAL)):
        raise ToolError('VERSION_CONFLICT', 'Task transition rejected')
    return target


def input_fingerprint(body):
    canonical = {**body, 'parent_node_id': body.get('parent_node_id')}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


class TaskStore(WorkspaceRepository):
    max_active_tasks = 2

    def initialize(self):
        super().initialize()
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks ('
                       'id TEXT PRIMARY KEY,owner TEXT NOT NULL,workspace TEXT NOT NULL,request_id TEXT NOT NULL,'
                       'fingerprint TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,version INTEGER NOT NULL,'
                       'lease_token TEXT,lease_expiry REAL,cancel_requested INTEGER NOT NULL DEFAULT 0,'
                       'response TEXT,created REAL NOT NULL,updated REAL NOT NULL,'
                       'UNIQUE(owner,workspace,request_id),'
                       'FOREIGN KEY(owner,workspace) REFERENCES workspaces(owner,id))')
            db.execute('CREATE TABLE IF NOT EXISTS submit_limits ('
                       'scope TEXT PRIMARY KEY,started REAL NOT NULL,attempts INTEGER NOT NULL)')

    def resource(self, owner, ref):
        if ref.kind != 'task':
            return super().resource(owner, ref)
        with self.transaction() as db:
            row = db.execute('SELECT owner,workspace,id FROM tasks WHERE owner=? AND workspace=? AND id=?',
                             (owner, ref.workspace, ref.id)).fetchone()
        return dict(row) if row else None

    def create_or_get(self, owner, workspace, body):
        fingerprint = input_fingerprint(body)
        now = self.clock()
        with self.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE owner=? AND workspace=? AND request_id=?',
                             (owner, workspace, body['request_id'])).fetchone()
            if row:
                if row['fingerprint'] != fingerprint:
                    raise ToolError('REQUEST_CONFLICT', 'Request ID belongs to different input')
                return dict(row), False
            db.execute('DELETE FROM submit_limits WHERE started<=?', (now - 60,))
            for scope, maximum in [('global', 12), ('owner:' + owner, 6)]:
                limit = db.execute('SELECT attempts FROM submit_limits WHERE scope=?', (scope,)).fetchone()
                if limit and limit[0] >= maximum:
                    raise ToolError('RATE_LIMIT', 'Submission rate exceeded')
            if db.execute('SELECT count(*) FROM tasks WHERE owner=?', (owner,)).fetchone()[0] >= 128:
                raise ToolError('RESOURCE_LIMIT', 'Task capacity reached')
            # No waiting queue: reserve one owner/global slot atomically at acceptance.
            active = db.execute("SELECT owner FROM tasks WHERE status IN ('accepted','running')").fetchall()
            if len(active) >= self.max_active_tasks or any(r['owner'] == owner for r in active):
                raise ToolError('BUSY', 'Task capacity busy')
            task_id = uuid.uuid4().hex
            for scope in ('global', 'owner:' + owner):
                db.execute('INSERT INTO submit_limits VALUES(?,?,1) ON CONFLICT(scope) '
                           'DO UPDATE SET attempts=submit_limits.attempts+1', (scope, now))
            db.execute('INSERT INTO tasks(id,owner,workspace,request_id,fingerprint,body,status,version,lease_expiry,created,updated) '
                       "VALUES(?,?,?,?,?,?,'accepted',0,?,?,?)",
                       (task_id, owner, workspace, body['request_id'], fingerprint, pack(body), now + 75, now, now))
            return dict(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()), True

    def get_authorized(self, owner, workspace, task_id):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE owner=? AND workspace=? AND id=?',
                             (owner, workspace, task_id)).fetchone()
        if row is None:
            raise ToolError('NOT_FOUND', 'Resource not found')
        return dict(row)

    def claim(self, task_id):
        now, token = self.clock(), uuid.uuid4().hex
        with self.transaction() as db:
            changed = db.execute("UPDATE tasks SET status='running',version=version+1,lease_token=?,updated=? "
                                 "WHERE id=? AND status='accepted' AND cancel_requested=0 AND lease_expiry>?",
                                 (token, now, task_id, now)).rowcount
            if changed != 1:
                raise ToolError('VERSION_CONFLICT', 'Task already claimed or expired')
            return dict(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def check_lease(self, task):
        current = self.get_authorized(task['owner'], task['workspace'], task['id'])
        if (current['status'] != 'running' or current['version'] != task['version']
                or current['lease_token'] != task['lease_token'] or current['lease_expiry'] <= self.clock()
                or current['cancel_requested']):
            raise ToolError('INTERRUPTED', 'Task stopped or lease expired')
        return current

    def finish_if_owner_version(self, task, response, node=None):
        target, now = terminal_status(response), self.clock()
        with self.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
            if (row is None or row['status'] != 'running' or row['owner'] != task['owner']
                    or row['version'] != task['version'] or row['lease_token'] != task['lease_token']
                    or row['lease_expiry'] <= now):
                raise ToolError('VERSION_CONFLICT', 'Task lease no longer owns result')
            if row['cancel_requested']:
                target, response, node = 'cancelled', {'state': 'cancelled'}, None
            if node:
                version = db.execute('SELECT version FROM workspaces WHERE owner=? AND id=?',
                                     (task['owner'], task['workspace'])).fetchone()[0]
                payload = pack(node)
                self._save_node(db, task['owner'], task['workspace'], node, payload,
                                len(payload.encode()), version)
            db.execute('UPDATE tasks SET status=?,response=?,version=version+1,updated=? WHERE id=?',
                       (target, pack(response), now, task['id']))
        return self.get_authorized(task['owner'], task['workspace'], task['id'])

    def request_cancel(self, owner, workspace, task_id):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE owner=? AND workspace=? AND id=?',
                             (owner, workspace, task_id)).fetchone()
            if row is None:
                raise ToolError('NOT_FOUND', 'Resource not found')
            if row['status'] not in TERMINAL:
                db.execute("UPDATE tasks SET cancel_requested=1,updated=?,"
                           "response=CASE WHEN status='accepted' THEN '{\"state\":\"cancelled\"}' ELSE response END,"
                           "version=CASE WHEN status='accepted' THEN version+1 ELSE version END,"
                           "status=CASE WHEN status='accepted' THEN 'cancelled' ELSE status END WHERE id=?",
                           (self.clock(), task_id))
        return self.get_authorized(owner, workspace, task_id)

    def list_tasks(self, owner, workspace):
        with self.transaction() as db:
            return [dict(row) for row in db.execute('SELECT * FROM tasks WHERE owner=? AND workspace=? ORDER BY created',
                                                    (owner, workspace)).fetchall()]

    def recover_expired(self):
        with self.transaction() as db:
            return db.execute("UPDATE tasks SET status='interrupted',version=version+1,updated=?,"
                              "response=? WHERE status IN ('accepted','running') AND lease_expiry<=?",
                              (self.clock(), pack({'state': 'interrupted'}), self.clock())).rowcount

    def interrupt_inflight(self):
        """One-time process-start recovery. Never dispatch or replay old work."""
        with self.transaction() as db:
            return db.execute("UPDATE tasks SET status='interrupted',version=version+1,updated=?,"
                              "response=? WHERE status IN ('accepted','running')",
                              (self.clock(), pack({'state': 'interrupted'}))).rowcount
