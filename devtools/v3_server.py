"""Isolated four-thread local acceptance server. Qwen is always an offline stub."""
import json
from pathlib import Path
import secrets
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from devtools.run_local import configure_offline


class OfflineModel:
    def get_completion(self, messages, **kwargs):
        prompt, payload = messages[0]['content'], json.loads(messages[-1]['content'])
        if 'Your role is Planner' in prompt:
            value = {'action': 'analyze', 'canonical_question': payload['question'], 'question': ''}
        elif 'Your role is an independent semantic Reviewer' in prompt:
            value = {'decision': 'approve', 'question': ''}
        else:
            value = {'fact_ids': list(payload['facts'])[:3]}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))


def main():
    configure_offline()
    directory = Path(sys.argv[1]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    from data_formulator.ecommerce.task_store import TaskStore
    from data_formulator.ecommerce.atomic_budget import AtomicBudget
    from data_formulator.ecommerce.multiuser_app import create_app
    from data_formulator.ecommerce.multiuser_service import MultiuserService
    from data_formulator.ecommerce.task_service import TaskService
    from data_formulator.ecommerce.multiuser_routes import install_routes
    from flask import send_from_directory
    store = TaskStore(directory / 'multiuser.sqlite')
    store.initialize()
    # Credentials enter through a private stdin pipe, never command line/log/file.
    credentials = json.loads(sys.stdin.readline())
    with store.transaction() as db:
        provisioned = db.execute('SELECT count(*) FROM accounts').fetchone()[0]
    if not provisioned:
        owners = [store.create(name, password) for name, password in credentials['accounts'].items()]
        source = directory / 'synthetic-usage.json'
        source.write_text(json.dumps([{'attempt': i+1, 'reserved_cny': .02} for i in range(5)]))
        budget = AtomicBudget(store, source)
        budget.migrate_copy(live_cap_cny=3)
        for owner in owners:
            budget.set_user_cap(owner, 1.5)
    budget = AtomicBudget(store, directory / 'synthetic-usage.json')
    business = MultiuserService(store, directory / 'audit', lambda *args: OfflineModel())
    service = TaskService(store, business, budget)
    app = create_app(directory, secret_key=credentials['secret'])
    install_routes(app, service)
    dist = Path(__file__).resolve().parents[1] / 'py-src/data_formulator/dist'
    @app.get('/multiuser')
    def page():
        return send_from_directory(dist, 'index.html')
    @app.get('/<path:name>')
    def asset(name):
        return send_from_directory(dist, name)
    serve(app, service)


def serve(app, service):
    from werkzeug.serving import BaseWSGIServer, WSGIRequestHandler
    class QuietHandler(WSGIRequestHandler):
        def log_request(self, code='-', size='-'):
            pass
    class BoundedServer(BaseWSGIServer):
        multithread = True
        request_queue_size = 8
        def __init__(self):
            super().__init__('127.0.0.1', 5567, app, handler=QuietHandler)
            self.workers = ThreadPoolExecutor(max_workers=4)
            self.capacity = threading.BoundedSemaphore(4)
        def process_request(self, request, address):
            # Browser startup requests several assets concurrently. Bounded
            # accept-side wait avoids dropping assets while retaining four workers.
            if not self.capacity.acquire(timeout=1):
                self.shutdown_request(request)
                return
            def process():
                try:
                    request.settimeout(65)
                    self.finish_request(request, address)
                except Exception:
                    self.handle_error(request, address)
                finally:
                    self.shutdown_request(request)
                    self.capacity.release()
            self.workers.submit(process)
    try:
        BoundedServer().serve_forever()
    finally:
        service.close()


if __name__ == '__main__':
    main()
