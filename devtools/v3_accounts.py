"""Provision or disable invited local accounts; prompts never echo passwords."""
import argparse
import getpass
from pathlib import Path

from devtools.run_local import ROOT, configure_offline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'disable', 'password'])
    parser.add_argument('username')
    parser.add_argument('--directory', type=Path, default=ROOT / '.local/v3')
    args = parser.parse_args()
    configure_offline()
    from data_formulator.ecommerce.task_store import TaskStore
    directory = args.directory.resolve()
    if directory == (ROOT / '.local/runtime').resolve() or (ROOT / '.local/runtime').resolve() in directory.parents:
        raise ValueError('Do not provision inside the legacy runtime')
    store = TaskStore(directory / 'multiuser.sqlite')
    store.initialize()
    if args.action == 'create':
        password = getpass.getpass('New password: ')
        if password != getpass.getpass('Repeat password: '):
            raise ValueError('Passwords do not match')
        store.create(args.username, password)
    else:
        with store.transaction() as db:
            account = db.execute('SELECT id FROM accounts WHERE username=?', (args.username,)).fetchone()
        if not account:
            raise ValueError('Account not found')
        if args.action == 'disable':
            store.revoke(account['id'], disable=True)
        else:
            password = getpass.getpass('New password: ')
            if password != getpass.getpass('Repeat password: '):
                raise ValueError('Passwords do not match')
            store.change_password(account['id'], password)
    print('Account updated. No model calls or historical ledger changes.')


if __name__ == '__main__':
    main()
