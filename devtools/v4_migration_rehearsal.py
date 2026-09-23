"""Read-only source snapshot to a new local PostgreSQL schema; never activates it."""
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

ROOT = Path(__file__).resolve().parents[1]
TABLES = ('accounts', 'login_limits', 'auth_sessions', 'workspaces', 'nodes',
          'tasks', 'submit_limits', 'website_usage')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def rows_digest(rows):
    return digest(json.dumps(rows, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def main():
    from devtools.run_local import configure_offline
    configure_offline()
    import psycopg2
    from psycopg2.extensions import make_dsn
    from dotenv import dotenv_values
    from data_formulator.ecommerce.postgres_store import PostgresTaskStore
    from data_formulator.ecommerce.website_usage import WebsiteUsage
    run_id = uuid.uuid4().hex[:12]
    directory = ROOT / '.local/v4-migration' / run_id
    directory.mkdir(parents=True, exist_ok=False)
    report_file = ROOT / 'docs/verification/V4-S01' / f'migration-rehearsal-{run_id}.json'
    source = ROOT / '.local/v3/multiuser.sqlite'
    ledger = ROOT / '.local/verification/qwen-usage.json'
    original = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in
                (source, ledger, source.with_name(source.name + '-wal')) if p.exists()}
    report = {'status': 'failed', 'scope': 'local-isolated-copy-only', 'model_calls': 0,
              'source_hashes_before': original, 'checks': {}}
    store = admin = None
    schema = 'v4_migration_' + run_id
    try:
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
            with sqlite3.connect(directory / 'snapshot.sqlite') as snapshot:
                src.backup(snapshot)
        snapshot = sqlite3.connect((directory / 'snapshot.sqlite').as_uri() + '?mode=ro', uri=True)
        snapshot.row_factory = sqlite3.Row
        try:
            assert snapshot.execute('pragma integrity_check').fetchone()[0] == 'ok'
            assert not snapshot.execute('pragma foreign_key_check').fetchall()
            tables = {r[0] for r in snapshot.execute("select name from sqlite_master where type='table'")}
            assert tables == set(TABLES), 'Unreviewed source schema; refuse partial migration'
            rows = {t: [dict(r) for r in snapshot.execute('SELECT * FROM ' + t + ' ORDER BY rowid')] for t in TABLES}
            assert all(r['status'] not in ('accepted', 'running') for r in rows['tasks']), 'Active source tasks require a quiesced cutover'
        finally:
            snapshot.close()
        config = dotenv_values(ROOT / 'deploy/ecommerce/.env.v4.private')
        dsn = make_dsn(host='127.0.0.1', port=config['V4_POSTGRES_PORT'],
                       dbname=config['V4_POSTGRES_DB'], user=config['V4_POSTGRES_USER'],
                       password=config['V4_POSTGRES_PASSWORD'])
        admin = psycopg2.connect(dsn, connect_timeout=2)
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute('CREATE SCHEMA "' + schema + '"')
        store = PostgresTaskStore(dsn, schema=schema)
        store.initialize()
        WebsiteUsage(store)
        with store.transaction() as db:
            for table in TABLES:
                assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
                for row in rows[table]:
                    columns = list(row)
                    db.execute('INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES ('
                               + ','.join('?' for _ in columns) + ')', tuple(row[c] for c in columns))
            # Keep the debugging campaign as immutable bytes, separate from website usage.
            db.execute('CREATE TABLE legacy_debug_archive (id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, payload BYTEA NOT NULL)')
            raw = ledger.read_bytes()
            db.execute('INSERT INTO legacy_debug_archive VALUES(1,?,?)', (digest(raw), psycopg2.Binary(raw)))
        with store.transaction() as db:
            for table in TABLES:
                restored = [dict(row) for row in db.execute('SELECT * FROM ' + table).fetchall()]
                if table == 'nodes':
                    restored = [dict(row) for row in db.execute('SELECT * FROM nodes ORDER BY position').fetchall()]
                    for row in restored:
                        row.pop('position')
                else:
                    restored.sort(key=lambda row: json.dumps(row, sort_keys=True))
                expected = list(rows[table])
                if table != 'nodes':
                    expected.sort(key=lambda row: json.dumps(row, sort_keys=True))
                assert restored == expected, 'Row conservation failed: ' + table
                report['checks'][table] = {'rows': len(restored), 'equal': True,
                                          'sha256': rows_digest(restored)}
            archived = db.execute('SELECT sha256,payload FROM legacy_debug_archive WHERE id=1').fetchone()
            assert archived['sha256'] == digest(raw) and bytes(archived['payload']) == raw
            report['checks']['legacy_debug_ledger_byte_exact'] = True
        after = {path: digest((ROOT / path).read_bytes()) for path in original}
        assert after == original, 'Source changed during rehearsal; do not activate'
        report.update(status='passed', source_hashes_after=after,
                      limitations=['No production activation or writer cutover.',
                                   'Copied sessions are isolated; revoke and rebuild at production cutover.',
                                   'Legacy V1 unowned history needs explicit owner mapping.'])
    except Exception as error:
        report['error_type'] = type(error).__name__
        # Never echo DSNs, password hashes, rows or exception strings.
    finally:
        if store:
            store.close()
        if admin:
            admin.close()
        report['retained_isolated_schema'] = schema
        with report_file.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
        print(json.dumps({'status': report['status'], 'report': str(report_file.relative_to(ROOT))}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
