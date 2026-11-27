"""The shipped offline fixture must work with the current evidence validator."""
import importlib.util
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location(
    'business_fixture', Path(__file__).resolve().parents[1] / 'scripts/preview_business_fixture.py'
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def post(client, record_id, revision, action='confirm', correction=None, key=None):
    body = dict(action=action, record_id=record_id, expected_revision=revision,
                evidence_ids=['fixture_evidence'], reason='离线核对原表、单位与年度')
    if correction is not None:
        body['correction'] = correction
    return client.post('/tasks/offline_browser_fixture/metric-reviews', json=body,
                       headers={'Idempotency-Key': key or str(uuid4())})


def test_fixture_confirm_marker_correct_audit_and_exports(tmp_path):
    app = fixture.fixture_app(tmp_path)
    client = TestClient(app)
    assert client.get('/?task_id=offline_browser_fixture').status_code == 200
    for index in range(3):
        response = post(client, f'fixture_{index}', 7 + index)
        assert response.status_code == 200, response.text
    key = str(uuid4())
    correction = dict(status='extracted', raw_value='0.1300', value='0.1300')
    saved = post(client, 'fixture_1', 10, 'correct', correction, key)
    assert saved.status_code == 200, saved.text
    record = saved.json()['data']['record']
    assert record['origin'] == 'manual' and record['value'] == '0.1300'
    assert post(client, 'fixture_1', 10, 'correct', correction, key).json() == saved.json()
    events = client.get('/tasks/offline_browser_fixture/metric-reviews').json()['data']['events']
    assert len(events) == 4
    export = client.post('/tasks/offline_browser_fixture/exports', json={'expected_revision':11},
                         headers={'Idempotency-Key':str(uuid4())})
    assert export.status_code == 200, export.text
    artifacts = app.state.artifacts.list_for_task('offline_browser_fixture')
    assert len(artifacts) == 3 and all(a['is_current'] for a in artifacts)


def test_uncertain_fixture_replay_commits_only_once(tmp_path):
    app = fixture.fixture_app(tmp_path, uncertain_first=True)
    client = TestClient(app)
    client.get('/')
    key = str(uuid4())
    assert post(client, 'fixture_0', 7, key=key).status_code == 503
    replay = post(client, 'fixture_0', 7, key=key)
    assert replay.status_code == 200 and replay.json()['revision'] == 8
    assert len(client.get('/tasks/offline_browser_fixture/metric-reviews').json()['data']['events']) == 1
