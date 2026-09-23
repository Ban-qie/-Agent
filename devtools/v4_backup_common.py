"""Shared validation and inventory helpers for V4 backup/restore CLIs."""
import base64
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re


SCHEMA_RE = re.compile(r'^[a-z][a-z0-9_]{0,62}$')
DATABASE_RE = re.compile(r'^v4_restore_[a-z0-9_]{1,48}$')


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def confined(path, root, *, must_exist=False):
    root = Path(root).resolve()
    path = Path(path).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Path must be a child of the designated root')
    if must_exist and not path.exists():
        raise ValueError('Required path does not exist')
    return path


def normalize(value):
    if isinstance(value, memoryview):
        value = bytes(value)
    if isinstance(value, bytes):
        return {'base64': base64.b64encode(value).decode('ascii')}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def rows_digest(rows):
    values = []
    for row in rows:
        item = {key: normalize(value) for key, value in dict(row).items()}
        values.append(json.dumps(item, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')))
    values.sort()
    return hashlib.sha256('\n'.join(values).encode('utf-8')).hexdigest()


def database_inventory(cursor, schema):
    if not SCHEMA_RE.fullmatch(schema):
        raise ValueError('Invalid schema')
    cursor.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name", (schema,))
    tables = [row[0] for row in cursor.fetchall()]
    inventory = {}
    for table in tables:
        cursor.execute('SELECT * FROM "' + schema + '"."' + table + '"')
        rows = cursor.fetchall()
        columns = [column.name for column in cursor.description]
        inventory[table] = {
            'rows': len(rows),
            'sha256': rows_digest(dict(zip(columns, row)) for row in rows),
        }
    return inventory


def file_manifest(root):
    root = Path(root).resolve()
    return {
        path.relative_to(root).as_posix(): {
            'bytes': path.stat().st_size,
            'sha256': sha256_file(path),
        }
        for path in sorted(root.rglob('*')) if path.is_file()
    }


def verify_files(root, expected):
    actual = file_manifest(root)
    if actual != expected:
        raise ValueError('Backup file inventory does not match the manifest')
    return actual

