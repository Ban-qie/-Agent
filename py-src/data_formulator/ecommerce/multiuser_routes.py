"""Exact isolated HTTP routes; business graph runs only behind verified sessions."""
from flask import g, jsonify, request

from data_formulator.ecommerce.authorization import Principal
from data_formulator.ecommerce.contracts import ToolError


def install_routes(app, service):
    app.extensions['v3_service'] = service

    @app.get('/api/ecommerce/catalog')
    def catalog():
        from data_formulator.ecommerce.executor import accepted_catalog
        from data_formulator.ecommerce.metrics import METRIC_CONTRACT
        return jsonify(snapshots=[{'snapshot_id': key, 'observed_dates': value['quality']}
                                  for key, value in accepted_catalog().items()], metric_contract=METRIC_CONTRACT)

    @app.errorhandler(ToolError)
    def domain_error(error):
        status = {'NOT_FOUND': 404, 'AUTH_REQUIRED': 401, 'ACCESS_DENIED': 403,
                  'BUSY': 429, 'RESOURCE_LIMIT': 429, 'REQUEST_CONFLICT': 409,
                  'VERSION_CONFLICT': 409, 'MODEL_DISABLED': 503, 'RATE_LIMIT': 429,
                  'BUDGET_EXHAUSTED': 429, 'BUDGET_UNAVAILABLE': 503}.get(error.code, 400)
        return jsonify(error={'code': error.code, 'message': error.message}), status

    @app.get('/api/ecommerce/workspace')
    def workspace():
        principal = Principal(g.v3_principal['id'])
        state = service.workspace(principal).read()
        if hasattr(service, 'store'):
            from data_formulator.ecommerce.task_service import public_task
            state['tasks'] = [public_task(task) for task in service.store.list_tasks(principal.owner, 'ecommerce-v0')]
        return jsonify(state)

    @app.get('/api/ecommerce/tasks/<task_id>')
    def task_status(task_id):
        from data_formulator.ecommerce.task_service import public_task
        return jsonify(public_task(service.get(Principal(g.v3_principal['id']), task_id)))

    @app.post('/api/ecommerce/tasks/<task_id>/cancel')
    def cancel(task_id):
        from data_formulator.ecommerce.task_service import public_task
        return jsonify(public_task(service.cancel(Principal(g.v3_principal['id']), task_id)))

    @app.post('/api/ecommerce/analyze')
    def analyze():
        if hasattr(service, 'submit'):
            from data_formulator.ecommerce.task_service import public_task
            from data_formulator.ecommerce.task_store import TERMINAL
            task = service.submit(Principal(g.v3_principal['id']), request.get_json(silent=True))
            if task['status'] in TERMINAL and task['response']:
                import json
                result = json.loads(task['response'])
                return jsonify(result), 422 if result.get('state') in ('failed', 'partial') else 200
            return jsonify(public_task(task)), 202
        result = service.analyze(Principal(g.v3_principal['id']), request.get_json(silent=True))
        return jsonify(result), 422 if result.get('state') == 'failed' else 200
