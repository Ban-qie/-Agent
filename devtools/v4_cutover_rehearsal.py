"""Rehearse the V3-to-V4 cutover in a new schema without touching the source."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import uuid


ROOT = Path(__file__).resolve().parents[1]
TABLES = ('accounts', 'login_limits', 'auth_sessions', 'workspaces', 'nodes',
          'tasks', 'submit_limits', 'website_usage')
OWNER_TABLES = ('auth_sessions', 'workspaces', 'nodes', 'tasks', 'website_usage')


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def load_source(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('source-integrity')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('source-foreign-key')
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != set(TABLES):
            raise ValueError('source-schema')
        rows = {table: [dict(row) for row in connection.execute(
            'SELECT * FROM ' + table + ' ORDER BY rowid')] for table in TABLES}
        if any(row['status'] in ('accepted', 'running') for row in rows['tasks']):
            raise ValueError('source-active-task')
        if any(row['id'].startswith('local:') for row in rows['accounts']):
            raise ValueError('source-local-identity')
        if any(str(row.get('owner', '')).startswith('local:')
               for table in OWNER_TABLES for row in rows[table]):
            raise ValueError('source-local-owner')
        return rows
    finally:
        connection.close()


def expect_rejected(path, expected):
    try:
        load_source(path)
    except ValueError as error:
        return str(error) == expected
    return False


def expect_damaged_rejected(path):
    try:
        load_source(path)
    except (sqlite3.DatabaseError, ValueError):
        return True
    return False


def main():
    from devtools.run_local import configure_offline
    configure_offline()
    import psycopg2
    from dotenv import dotenv_values
    from psycopg2.extensions import make_dsn
    from data_formulator.ecommerce.postgres_store import PostgresTaskStore
    from data_formulator.ecommerce.website_usage import WebsiteUsage

    run_id = uuid.uuid4().hex[:12]
    directory = ROOT / '.local/v4-cutover' / run_id
    directory.mkdir(parents=True, exist_ok=False)
    report_file = ROOT / 'docs/verification/V4-S01' / f'cutover-rehearsal-{run_id}.json'
    source = ROOT / '.local/v3/multiuser.sqlite'
    ledger = ROOT / '.local/verification/qwen-usage.json'
    tracked = tuple(path for path in (source, source.with_name(source.name + '-wal'), ledger)
                    if path.exists())
    before = {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in tracked}
    schema = 'v4_cutover_' + run_id
    report = {'status': 'failed', 'scope': 'local-isolated-cutover-rehearsal',
              'schema': schema, 'model_calls': 0, 'source_hashes_before': before,
              'negative_cases': {}, 'checks': {}}
    store = admin = None
    try:
        snapshot_path = directory / 'source.sqlite'
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as live:
            with sqlite3.connect(snapshot_path) as snapshot:
                live.backup(snapshot)
        rows = load_source(snapshot_path)

        unknown_schema = directory / 'unknown-schema.sqlite'
        shutil.copy2(snapshot_path, unknown_schema)
        with sqlite3.connect(unknown_schema) as database:
            database.execute('CREATE TABLE unreviewed_payload(value TEXT)')
        report['negative_cases']['unknown_table_rejected'] = expect_rejected(
            unknown_schema, 'source-schema')

        active_task = directory / 'active-task.sqlite'
        shutil.copy2(snapshot_path, active_task)
        with sqlite3.connect(active_task) as database:
            database.execute("UPDATE tasks SET status='running' WHERE rowid=(SELECT min(rowid) FROM tasks)")
        report['negative_cases']['active_task_rejected'] = expect_rejected(
            active_task, 'source-active-task')

        local_owner = directory / 'local-owner.sqlite'
        shutil.copy2(snapshot_path, local_owner)
        with sqlite3.connect(local_owner) as database:
            database.execute("UPDATE accounts SET id='local:discarded'")
            for table in OWNER_TABLES:
                database.execute("UPDATE " + table + " SET owner='local:discarded'")
        report['negative_cases']['v1_v2_local_owner_rejected'] = expect_rejected(
            local_owner, 'source-local-identity')

        damaged = directory / 'damaged.sqlite'
        payload = bytearray(snapshot_path.read_bytes())
        payload[:16] = b'not-a-sqlite-db!'
        damaged.write_bytes(payload)
        report['negative_cases']['damaged_source_rejected'] = expect_damaged_rejected(damaged)

        config = dotenv_values(ROOT / 'deploy/ecommerce/.env.v4.private')
        dsn = make_dsn(host='127.0.0.1', port=config['V4_POSTGRES_PORT'],
                       dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
                       password=config['V4_POSTGRES_PASSWORD'])
        admin = psycopg2.connect(dsn, connect_timeout=2)
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute('CREATE SCHEMA "' + schema + '"')
            try:
                cursor.execute('CREATE SCHEMA "' + schema + '"')
            except psycopg2.errors.DuplicateSchema:
                report['negative_cases']['duplicate_target_rejected'] = True
            else:
                report['negative_cases']['duplicate_target_rejected'] = False
        store = PostgresTaskStore(dsn, schema=schema)
        store.initialize()
        WebsiteUsage(store)
        account = rows['accounts'][0]
        try:
            with store.transaction() as database:
                columns = list(account)
                database.execute('INSERT INTO accounts(' + ','.join(columns) + ') VALUES ('
                                 + ','.join('?' for _ in columns) + ')',
                                 tuple(account[column] for column in columns))
                raise RuntimeError('synthetic-cutover-failure')
        except RuntimeError:
            pass
        with store.transaction() as database:
            report['negative_cases']['transaction_failure_rolled_back'] = (
                database.execute('SELECT count(*) FROM accounts').fetchone()[0] == 0)
        if not all(report['negative_cases'].values()):
            raise AssertionError('migration-negative-case')
        raw_ledger = ledger.read_bytes()
        ledger_rows = json.loads(raw_ledger)
        unknown_usage = sum(1 for row in ledger_rows if row.get('input_tokens') is None
                            or row.get('output_tokens') is None)
        with store.transaction() as database:
            for table in TABLES:
                selected = [] if table == 'auth_sessions' else rows[table]
                for row in selected:
                    columns = list(row)
                    database.execute('INSERT INTO ' + table + '(' + ','.join(columns)
                                     + ') VALUES (' + ','.join('?' for _ in columns) + ')',
                                     tuple(row[column] for column in columns))
            database.execute('CREATE TABLE legacy_debug_archive ('
                             'id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL,payload BYTEA NOT NULL)')
            database.execute('INSERT INTO legacy_debug_archive VALUES(1,?,?)',
                             (digest(raw_ledger), psycopg2.Binary(raw_ledger)))

        with store.transaction() as database:
            for table in TABLES:
                if table == 'nodes':
                    restored = [dict(row) for row in database.execute(
                        'SELECT * FROM nodes ORDER BY position').fetchall()]
                    for row in restored:
                        row.pop('position')
                else:
                    restored = [dict(row) for row in database.execute(
                        'SELECT * FROM ' + table).fetchall()]
                    restored.sort(key=lambda row: json.dumps(row, sort_keys=True))
                expected = [] if table == 'auth_sessions' else list(rows[table])
                if table != 'nodes':
                    expected.sort(key=lambda row: json.dumps(row, sort_keys=True))
                if restored != expected:
                    raise AssertionError('row-conservation-' + table)
                report['checks'][table] = {'source_rows': len(rows[table]),
                                           'target_rows': len(restored),
                                           'equal_after_session_policy': True}
            local_count = database.execute(
                'SELECT count(*) FROM accounts WHERE id LIKE ?', ('local:%',)).fetchone()[0]
            for table in OWNER_TABLES:
                local_count += database.execute(
                    'SELECT count(*) FROM ' + table + ' WHERE owner LIKE ?',
                    ('local:%',)).fetchone()[0]
            archived = database.execute(
                'SELECT sha256,payload FROM legacy_debug_archive WHERE id=1').fetchone()
        if local_count != 0:
            raise AssertionError('local-history-imported')
        if archived['sha256'] != digest(raw_ledger) or bytes(archived['payload']) != raw_ledger:
            raise AssertionError('ledger-conservation')
        after = {path: digest((ROOT / path).read_bytes()) for path in before}
        if after != before:
            raise AssertionError('source-mutated')
        report['checks'].update(
            v1_v2_local_rows=local_count,
            sessions_revoked=(len(rows['auth_sessions']) > 0 and
                              report['checks']['auth_sessions']['target_rows'] == 0),
            legacy_debug_ledger_byte_exact=True,
            legacy_debug_records=len(ledger_rows),
            unknown_usage_records=unknown_usage)
        report.update(status='passed', source_hashes_after=after,
                      next_step='Activate this isolated schema locally, never on the remote target.')
    except Exception as error:
        report['error_type'] = type(error).__name__
    finally:
        if store:
            store.close()
        if admin:
            admin.close()
        with report_file.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
        print(json.dumps({'status': report['status'], 'report': str(report_file.relative_to(ROOT)),
                          'schema': schema}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
