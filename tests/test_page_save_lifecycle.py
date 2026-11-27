"""Save through the real page contract, restore state, and re-review invalidated checks."""
from uuid import uuid4
import hashlib
from esg_app.parse_repository import read_business
from fastapi.testclient import TestClient
from esg_app.config import Settings
from esg_app.main import create_app
from test_review_dispositions import setup, state


def test_save_refresh_audit_expiry_and_re_review(tmp_path):
    app,c=setup(tmp_path)
    original=c.post('/tasks/t/exports',headers={'X-User':'reviewer','Idempotency-Key':str(uuid4())},json={'expected_revision':0})
    assert original.status_code==200
    old=app.state.artifacts.list_for_task('t')
    body=dict(action='confirm',record_id='m1_2023',expected_revision=0,reason='page save audit',evidence_ids=['ev1'])
    key=str(uuid4());headers={'X-User':'reviewer','Idempotency-Key':key}
    saved=c.post('/tasks/t/metric-reviews',headers=headers,json=body)
    assert saved.status_code==200 and saved.json()['revision']==1
    assert c.post('/tasks/t/metric-reviews',headers=headers,json=body).json()==saved.json()
    html=c.get('/?task_id=t').text
    assert 'data-revision="1"'in html and '已过期'in html
    assert '已因指标变更过期，需重新复核'in html
    s=state(app);invalid=next(x for x in s['checks']if not x['is_current'])
    assert f'保存 {invalid["check_id"]} 复核'in html
    # Invalidated targets cannot be closed using the disposition form until explicitly reviewed.
    assert f'data-draft="disposition-check-{invalid["check_id"]}"' not in html
    audit=c.get('/tasks/t/metric-reviews',headers={'X-User':'reviewer'}).json()['data']['events']
    assert len(audit)==1 and audit[0]['reason']=='page save audit'
    for a in old:
        downloaded=c.get('/api/artifacts/'+a['artifact_id'],headers={'X-User':'reviewer'})
        assert downloaded.status_code==200 and downloaded.headers['X-Artifact-Current']=='false'
        assert hashlib.sha256(downloaded.content).hexdigest()==a['sha256']
    reviewed=c.post('/tasks/t/review',headers={'X-User':'reviewer'},json=dict(
        expected_revision=1,check_id=invalid['check_id'],new_status='unknown',reason='重新核查仍缺证，保留未知'))
    assert reviewed.status_code==200,reviewed.text
    current=next(x for x in state(app)['checks']if x['check_id']==invalid['check_id'])
    assert current['is_current'] and current['status']=='unknown'
    restarted=TestClient(create_app(Settings(database_path=str(tmp_path/'review.sqlite3'))))
    history=restarted.get('/tasks/t/metric-reviews',headers={'X-User':'reviewer'}).json()['data']['events']
    assert len(history)==1 and history[0]['revision']==1
    assert 'data-revision="2"'in restarted.get('/?task_id=t').text
    with restarted.app.state.tasks.transaction() as db:
        persisted=read_business(db,'t')
    assert persisted['review']['events'][-1]['reason']=='重新核查仍缺证，保留未知'
