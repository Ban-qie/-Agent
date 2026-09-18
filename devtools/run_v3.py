"""Run the authenticated local V3 website with real Qwen (no startup calls)."""
import argparse
from pathlib import Path
import secrets

from devtools.run_local import ROOT


def build_app(directory, secret_key):
    from flask import redirect, send_from_directory
    from data_formulator.ecommerce.multiuser_app import create_app
    from data_formulator.ecommerce.multiuser_routes import install_routes
    from data_formulator.ecommerce.multiuser_service import MultiuserService
    from data_formulator.ecommerce.qwen_client import configured_qwen
    from data_formulator.ecommerce.task_service import TaskService
    from data_formulator.ecommerce.task_store import TaskStore
    from data_formulator.ecommerce.website_usage import WebsiteUsage
    directory = Path(directory).resolve()
    app = create_app(directory, secret_key=secret_key)
    store = TaskStore(directory / 'multiuser.sqlite')
    store.initialize()
    store.recover_expired()
    service = TaskService(store, MultiuserService(store, directory / 'audit', configured_qwen),
                          WebsiteUsage(store))
    install_routes(app, service)
    dist = ROOT / 'py-src/data_formulator/dist'

    @app.get('/')
    def home():
        return redirect('/multiuser')

    @app.get('/multiuser')
    def page():
        return send_from_directory(dist, 'index.html')

    @app.get('/<path:name>')
    def asset(name):
        return send_from_directory(dist, name)

    return app, service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / '.local/v3')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not (directory / 'multiuser.sqlite').is_file():
        parser.error('Create an invited account with devtools.v3_accounts first.')
    from devtools.qwen_config import configure_qwen, read_user_key
    configure_qwen(read_user_key())
    secret_path = directory / 'session-secret'
    if not secret_path.exists():
        with secret_path.open('x', encoding='ascii') as handle:
            handle.write(secrets.token_hex(32))
    app, service = build_app(directory, secret_path.read_text(encoding='ascii').strip())
    from devtools.v3_server import serve
    print('V3 Qwen website: http://127.0.0.1:5567/ (invited accounts; website usage recorded separately)', flush=True)
    serve(app, service)


if __name__ == '__main__':
    main()
