"""SQLite ownership-scoped workspace storage; short versioned transactions."""
import json
import re
import sqlite3

from data_formulator.ecommerce.account_store import AccountStore
from data_formulator.ecommerce.contracts import ToolError
from data_formulator.ecommerce.executor import SNAPSHOT_ID
from data_formulator.ecommerce.v1_workspace import MAX_BYTES, MAX_NODES, STATES, NODE_ID


def pack(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class WorkspaceRepository(AccountStore):
    def initialize(self):
        super().initialize()
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workspaces ('
                       'owner TEXT NOT NULL REFERENCES accounts(id), id TEXT NOT NULL, '
                       'version INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,id), UNIQUE(owner))')
            db.execute('CREATE TABLE IF NOT EXISTS nodes ('
                       'owner TEXT NOT NULL, workspace TEXT NOT NULL, id TEXT NOT NULL, '
                       'parent TEXT, payload TEXT NOT NULL, bytes INTEGER NOT NULL, '
                       'PRIMARY KEY(owner,workspace,id), '
                       'FOREIGN KEY(owner,workspace) REFERENCES workspaces(owner,id), '
                       'FOREIGN KEY(owner,workspace,parent) REFERENCES nodes(owner,workspace,id))')

    def create_workspace(self, owner, workspace='ecommerce-v0'):
        if not isinstance(workspace, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', workspace):
            raise ToolError('INVALID_REQUEST', 'Invalid workspace')
        with self.transaction() as db:
            db.execute('INSERT INTO workspaces(owner,id) VALUES(?,?) ON CONFLICT(owner,id) DO NOTHING',
                       (owner, workspace))

    def resource(self, owner, ref):
        if ref.kind == 'snapshot':
            return {'approved_readonly': True} if ref.id == SNAPSHOT_ID else None
        with self.transaction() as db:
            if ref.kind == 'workspace':
                row = db.execute('SELECT owner,id AS workspace FROM workspaces '
                                 'WHERE owner=? AND id=? AND id=?', (owner, ref.workspace, ref.id)).fetchone()
            elif ref.kind == 'node':
                row = db.execute('SELECT owner,workspace,id FROM nodes WHERE owner=? AND workspace=? AND id=?',
                                 (owner, ref.workspace, ref.id)).fetchone()
            else:
                return None
        return dict(row) if row else None

    def read(self, owner, workspace):
        with self.transaction() as db:
            row = db.execute('SELECT version FROM workspaces WHERE owner=? AND id=?', (owner, workspace)).fetchone()
            if row is None:
                raise ToolError('NOT_FOUND', 'Resource not found')
            nodes = db.execute('SELECT payload FROM nodes WHERE owner=? AND workspace=? ORDER BY rowid',
                               (owner, workspace)).fetchall()
        return {'schema_version': 1, 'orchestrator': 'v1', 'workspace_id': workspace,
                'version': row['version'], 'nodes': [json.loads(n['payload']) for n in nodes]}

    def get_node(self, owner, workspace, node_id):
        with self.transaction() as db:
            row = db.execute('SELECT payload FROM nodes WHERE owner=? AND workspace=? AND id=?',
                             (owner, workspace, node_id)).fetchone()
        if not row:
            raise ToolError('NOT_FOUND', 'Resource not found')
        return json.loads(row['payload'])

    def save_node(self, owner, workspace, node, *, expected_version, before_commit=None):
        if (not isinstance(node, dict) or not isinstance(node.get('node_id'), str)
                or not NODE_ID.fullmatch(node['node_id']) or node.get('status') not in STATES | {'cancelled'}
                or not isinstance(node.get('conditions'), dict) or not isinstance(node.get('result'), dict)):
            raise ToolError('INVALID_REQUEST', 'Invalid node')
        payload = pack(node)
        size = len(payload.encode('utf-8'))
        with self.transaction() as db:
            self._save_node(db, owner, workspace, node, payload, size, expected_version)
            if before_commit:
                before_commit()
        return expected_version + 1

    def _save_node(self, db, owner, workspace, node, payload, size, expected_version):
        row = db.execute('SELECT version FROM workspaces WHERE owner=? AND id=?', (owner, workspace)).fetchone()
        if row is None:
            raise ToolError('NOT_FOUND', 'Resource not found')
        if row['version'] != expected_version:
            raise ToolError('VERSION_CONFLICT', 'Workspace changed')
        parent = node.get('parent_node_id')
        if parent and (parent == node['node_id'] or db.execute(
                'SELECT 1 FROM nodes WHERE owner=? AND workspace=? AND id=?', (owner, workspace, parent)).fetchone() is None):
            raise ToolError('NOT_FOUND', 'Resource not found')
        previous = db.execute('SELECT payload,bytes FROM nodes WHERE owner=? AND workspace=? AND id=?',
                              (owner, workspace, node['node_id'])).fetchone()
        if previous:
            old = json.loads(previous['payload'])
            if old['status'] != 'running' or old.get('question') != node.get('question') or old.get('parent_node_id') != parent:
                raise ToolError('REQUEST_CONFLICT', 'Node cannot be overwritten')
        count, total = db.execute('SELECT count(*),coalesce(sum(bytes),0) FROM nodes WHERE owner=? AND workspace=?',
                                  (owner, workspace)).fetchone()
        if count + (0 if previous else 1) > MAX_NODES or total - (previous['bytes'] if previous else 0) + size > MAX_BYTES:
            raise ToolError('RESOURCE_LIMIT', 'Workspace capacity reached')
        db.execute('INSERT INTO nodes VALUES(?,?,?,?,?,?) ON CONFLICT(owner,workspace,id) '
                   'DO UPDATE SET payload=excluded.payload,bytes=excluded.bytes',
                   (owner, workspace, node['node_id'], parent, payload, size))
        db.execute('UPDATE workspaces SET version=version+1 WHERE owner=? AND id=?', (owner, workspace))

    def backup(self, destination):
        # SQLite backup includes a consistent committed view, including WAL.
        with sqlite3.connect(self.path) as source, sqlite3.connect(destination) as target:
            source.backup(target)
