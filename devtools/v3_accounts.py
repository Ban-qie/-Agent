"""Provision or disable invited local accounts; prompts never echo passwords."""
import argparse
import getpass
import sys
from pathlib import Path

from devtools.run_local import ROOT, configure_offline


def prompt_password(label):
    """Read a password while showing one mask character per accepted key."""
    if sys.platform != 'win32':
        return getpass.getpass(label)
    import msvcrt
    sys.stdout.write(label)
    sys.stdout.flush()
    chars = []
    while True:
        key = msvcrt.getwch()
        if key in ('\r', '\n'):
            sys.stdout.write('\n')
            sys.stdout.flush()
            return ''.join(chars)
        if key == '\003':
            raise KeyboardInterrupt
        if key in ('\b', '\x7f'):
            if chars:
                chars.pop()
                sys.stdout.write('\b \b')
                sys.stdout.flush()
            continue
        if key in ('\x00', '\xe0'):
            msvcrt.getwch()  # consume the second byte of an extended key
            continue
        if key.isprintable():
            chars.append(key)
            sys.stdout.write('*')
            sys.stdout.flush()


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
        password = prompt_password('New password: ')
        if password != prompt_password('Repeat password: '):
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
            password = prompt_password('New password: ')
            if password != prompt_password('Repeat password: '):
                raise ValueError('Passwords do not match')
            store.change_password(account['id'], password)
    print('Account updated. No model calls or historical ledger changes.')


if __name__ == '__main__':
    main()
