from unittest.mock import Mock

import pytest

from data_formulator.ecommerce.authorization import Principal, ResourceRef, authorize, authorized_action
from data_formulator.ecommerce.contracts import ToolError


class FakeRepository:
    def resource(self, owner, ref):
        if ref.id == 'missing':
            return None
        if ref.kind == 'snapshot':
            return {'approved_readonly': ref.id == 'approved'}
        # Deliberately return unscoped metadata to verify policy checks too.
        return {'owner': ref.id[0], 'workspace': 'workspace', 'id': ref.id}


@pytest.mark.parametrize('kind,action', [('workspace', 'read'), ('workspace', 'create'),
    ('workspace', 'list'), ('node', 'read'), ('node', 'create'), ('node', 'parent'),
    ('node', 'replay'), ('task', 'read'), ('task', 'cancel'), ('task', 'replay')])
def test_owner_action_matrix(kind, action):
    repo = FakeRepository()
    assert authorize(Principal('A'), action, ResourceRef(kind, 'workspace', 'A-object'), repo)['owner'] == 'A'
    for object_id in ('B-object', 'missing'):
        side_effect = Mock()
        with pytest.raises(ToolError) as error:
            authorized_action(Principal('A'), action, ResourceRef(kind, 'workspace', object_id), repo, side_effect)
        assert error.value.code == 'NOT_FOUND'
        assert error.value.message == 'Resource not found'
        side_effect.assert_not_called()


def test_public_snapshot_and_unknown_actions():
    repo = FakeRepository()
    for owner in ('A', 'B'):
        assert authorize(Principal(owner), 'read', ResourceRef('snapshot', '', 'approved'), repo)
    for action, identity in [('read', 'unapproved'), ('write', 'approved')]:
        with pytest.raises(ToolError):
            authorize(Principal('A'), action, ResourceRef('snapshot', '', identity), repo)


def test_workspace_and_unverified_principal_rejected():
    with pytest.raises(ToolError):
        authorize(Principal('A'), 'read', ResourceRef('node', 'other-workspace', 'A-object'), FakeRepository())
    with pytest.raises(ToolError):
        authorize({'owner': 'A'}, 'read', ResourceRef('node', 'workspace', 'A-object'), FakeRepository())


@pytest.mark.parametrize('action', ['read', 'parent', 'replay', 'cancel'])
def test_stub_service_authorizes_before_sensitive_payload_and_side_effects(action, caplog):
    from flask import Flask, jsonify
    app = Flask(__name__)
    sensitive, reserve, graph, worker, cancel = (Mock() for _ in range(5))
    kind = 'task' if action == 'cancel' else 'node'
    @app.get('/object/<object_id>')
    def route(object_id):
        try:
            authorize(Principal('A'), action, ResourceRef(kind, 'workspace', object_id), FakeRepository())
        except ToolError:
            return jsonify(error='Resource not found'), 404
        sensitive()
        reserve()
        graph()
        worker()
        cancel()
        return jsonify(result='A-result')
    client = app.test_client()
    assert client.get('/object/B-object').status_code == 404
    assert client.get('/object/missing').status_code == 404
    for effect in (sensitive, reserve, graph, worker, cancel):
        effect.assert_not_called()
    assert 'B-result' not in caplog.text
    assert client.get('/object/A-object').json == {'result': 'A-result'}
    for effect in (sensitive, reserve, graph, worker, cancel):
        effect.assert_called_once()
