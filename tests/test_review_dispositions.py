"""Humanly reviewed uncertainty closes workflow, never factual uncertainty."""
import csv
import io
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from esg_app.agent_host import make_agent_starter
from esg_app.config import Settings
from esg_app.main import create_app
from esg_app.parse_repository import read_business, save_business
from esg_app.retrieval import Evidence, EvidenceStore
from esg_app.review_dispositions import current_dispositions, review_complete
from esg_app.run_jobs import RunStatus
from test_metrics import candidate


def setup(tmp_path):
    app = create_app(Settings(database_path=str(tmp_path / 'review.sqlite3')))
    root=Path(__file__).resolve().parents[1]
    metadata=json.loads((root/'contracts/examples/get_task_results.success.json').read_text())['data']
    metadata['task'].update(task_id='t',revision=0,report_ids=['r1'])
    metadata['reports'][0].update(report_id='r1',sha256='a'*64)
    app.state.tasks.register_task('t', 0, metadata)
    app.state.evidence = EvidenceStore([Evidence('ev1', 'r1', 127, None, 'table', '环境数据 2023 2024 100 tCO2e')])
    with app.state.tasks.transaction() as db:
        state = read_business(db, 't')
        for m in range(1, 7):
            for year in [2023, 2024]:
                r = candidate(metric_id=f'E0{m}', year=year, report_id='r1', evidence_ids=['ev1'])
                r.update(record_id=f'm{m}_{year}', task_id='t', review_state='confirmed', revision=0, is_current=True)
                state['records'].append(r)
        state['checks'] = [dict(check_id=c['check_id'], status='met', evidence_ids=['ev1'], reason='已有核查证据',
                               origin='manual', review_state='confirmed', revision=0, is_current=True,
                               rule_version='disclosure-v0.1', missing_information=[], suggestion='', target_key=None, task_id='t')
                           for c in app.state.rulebook['checks']]
        state['comparisons'] = []
        save_business(db, 't', state)
    return app, TestClient(app)


def state(app):
    with app.state.tasks.transaction() as db:
        return read_business(db, 't')


def submit(client, **changes):
    body = dict(expected_revision=0, target_type='check', target_id='C01', decision='insufficient_evidence',
                reason='已核查，公开资料不足', search_scope='登记报告物理127页及相关脚注',
                missing_material='企业内部因子表', limitations='不得将反推比值作为正式因子或作确定同比')
    body.update(changes)
    # Use the actual configured first check ID; tests do not assume external ID aliases.
    if body['target_id'] == 'C01':
        body['target_id'] = client.app.state.rulebook['checks'][0]['check_id']
    return client.post('/tasks/t/review-dispositions', headers={'X-User': 'reviewer'}, json=body)


def test_uncertainty_is_audited_persisted_and_not_a_known_score(tmp_path):
    app, c = setup(tmp_path)
    assert submit(c).status_code == 200
    s = state(app);check = s['checks'][0]
    assert check['status'] == 'unknown' and check['review_state'] == 'unreviewed'
    assert s.get('score') is None
    assert review_complete(s, check, 'check')
    d = current_dispositions(s)[0]
    assert d['actor'] == 'reviewer' and d['evidence_refs'][0]['pdf_page'] == 127
    assert d['report_versions'][0]['sha256'] == 'a'*64
    restored = create_app(Settings(database_path=str(tmp_path / 'review.sqlite3')))
    response = TestClient(restored).get('/tasks/t/review-dispositions', headers={'X-User': 'reviewer'})
    assert response.json()['data']['current'][0]['limitations'] == d['limitations']
    html = c.get('/?task_id=t').text
    assert '已人工核查，证据不足' in html and '不得将反推比值' in html
    assert '证据覆盖率</dt><dd>100.00%' not in html


@pytest.mark.parametrize('field', ['reason', 'search_scope', 'missing_material', 'limitations'])
def test_incomplete_public_data_review_is_rejected(tmp_path, field):
    app, c = setup(tmp_path)
    assert submit(c, **{field: ' '}).status_code == 422
    assert not current_dispositions(state(app))
    assert app.state.tasks.get('t').revision == 0


@pytest.mark.parametrize('headers', [{}, {'X-Agent-Token': 'any'}, {'Authorization': 'Bearer any'}, {'X-User': 'reviewer', 'X-Agent-Token': 'any'}])
def test_agents_and_unauthenticated_requests_cannot_close_review(tmp_path, headers):
    app, c = setup(tmp_path)
    r = c.post('/tasks/t/review-dispositions', headers=headers, json={})
    assert r.status_code in (401, 403)
    assert state(app).get('review_dispositions') is None


def test_version_conflict_foreign_target_and_value_override_rejected(tmp_path):
    app, c = setup(tmp_path)
    assert submit(c, expected_revision=7).status_code == 409
    assert submit(c, expected_revision=True).status_code == 422
    assert submit(c, target_id='foreign-record').status_code == 404
    assert submit(c, value='999').status_code == 422
    assert submit(c).status_code == 200
    assert submit(c).status_code == 409


def test_marker_preserved_metric_disposition_invalidates_dependencies(tmp_path):
    app, c = setup(tmp_path)
    app.state.evidence = EvidenceStore([Evidence('ev1','r1',127,None,'table','2023 范围3 / tCO2e')])
    with app.state.tasks.transaction() as db:
        s=read_business(db,'t');r=next(r for r in s['records'] if r['record_id']=='m3_2023')
        r.update(status='missing_marker', raw_value='/', value=None, review_state='unreviewed')
        save_business(db,'t',s)
    assert submit(c, target_type='metric', target_id='m3_2023', decision='confirm').status_code == 200
    assert submit(c, expected_revision=1, target_type='metric', target_id='m3_2023').status_code == 200
    s=state(app);r=next(r for r in s['records'] if r['record_id']=='m3_2023')
    assert (r['status'],r['raw_value'],r['value'],r['review_state']) == ('missing_marker','/',None,'unreviewed')
    assert review_complete(s,r,'metric')
    assert any(not x['is_current'] for x in s['checks'])


def test_changed_target_and_explicit_reopen_require_new_review(tmp_path):
    app, c = setup(tmp_path)
    assert submit(c).status_code == 200
    assert submit(c, expected_revision=1, decision='reopen').status_code == 200
    assert not current_dispositions(state(app))
    assert not review_complete(state(app),state(app)['checks'][0],'check')
    assert submit(c, expected_revision=2).status_code == 200
    with app.state.tasks.transaction() as db:
        s=read_business(db,'t');s['checks'][0]['evidence_ids']=[];save_business(db,'t',s)
    assert not current_dispositions(state(app))
    assert len(state(app)['review_dispositions']) == 3


@pytest.mark.parametrize('status,note', [('unknown','候选原值、单位或未经验证的换算无法与所引证据核对'),('parse_error','解析失败'),('conflict','冲突')])
def test_technical_failures_cannot_be_closed_as_public_data_shortage(tmp_path,status,note):
    app,c=setup(tmp_path)
    with app.state.tasks.transaction() as db:
        s=read_business(db,'t');s['records'][0].update(status=status, extraction_note=note);save_business(db,'t',s)
    assert submit(c,target_type='metric',target_id='m1_2023').status_code==422
    assert submit(c,target_type='metric',target_id='m1_2023',decision='confirm').status_code==422


def test_active_run_cannot_be_reviewed(tmp_path):
    app,c=setup(tmp_path);app.state.runs.start('t','s')
    assert submit(c).status_code==409


def test_limited_completion_requires_normal_sdk_and_current_export(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','offline-key')
    app,c=setup(tmp_path)
    assert submit(c).status_code==200
    run=app.state.runs.start('t','offline_session')
    def sdk(*args,**kw):
        headers={'X-Agent-Token':kw['token']}
        r=c.post('/agent/tool',headers=headers,json=dict(tool='get_task_results',arguments={}))
        assert r.json()['ok']
        r=c.post('/agent/tool',headers={**headers,'Idempotency-Key':str(uuid4())},json=dict(tool='export_analysis',arguments={'expected_revision':1}))
        assert r.json()['ok'],r.text
        return SimpleNamespace(finish_reason='completed',final_response='公开资料核查流程结束，因子仍未知，不作确定同比。')
    make_agent_starter(app,sdk)('t',run.run_id,'full_evaluation','')
    assert app.state.runs.get(run.run_id).status is RunStatus.COMPLETED_WITH_LIMITATIONS
    assert not state(app)['checks'][0]['review_state']=='confirmed'
    exported=app.state.artifacts.list_for_task('t')
    assert len(exported)==3
    for a in exported:
        raw=app.state.artifacts.get_content(a['artifact_id']).decode()
        if a['format']=='json':
            data=json.loads(raw);assert data['score']['final_score'] is None
            assert data['review_dispositions'][0]['decision']=='insufficient_evidence'
            assert data['checks'][0]['status']=='unknown'
        if a['format']=='markdown': assert '不得将反推比值' in raw
        if a['format']=='csv':
            rows=list(csv.DictReader(io.StringIO(raw)))
            assert rows[-1]['review_target_type']=='check'
            assert rows[-1]['use_limitations']=='不得将反推比值作为正式因子或作确定同比'


def test_failed_commit_rolls_back_review_and_revision(tmp_path, monkeypatch):
    app,c=setup(tmp_path)
    import esg_app.review_dispositions as module
    original=module.save_business
    def fail(*args):
        original(*args)
        raise RuntimeError('simulated persistence failure')
    monkeypatch.setattr(module,'save_business',fail)
    with pytest.raises(RuntimeError,match='simulated'):
        submit(c)
    assert app.state.tasks.get('t').revision==0
    assert not current_dispositions(state(app))
    assert state(app)['checks'][0]['status']=='met'


def test_missing_export_still_blocks_limited_completion(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','offline-key')
    app,c=setup(tmp_path);assert submit(c).status_code==200
    run=app.state.runs.start('t','s')
    def sdk(*args,**kw):
        assert c.post('/agent/tool',headers={'X-Agent-Token':kw['token']},
                      json=dict(tool='get_task_results',arguments={})).json()['ok']
        return SimpleNamespace(finish_reason='completed',final_response='有使用限制')
    with pytest.raises(RuntimeError,match='exported snapshot'):
        make_agent_starter(app,sdk)('t',run.run_id,'full_evaluation','')
    assert app.state.runs.get(run.run_id).status is RunStatus.RUNNING


def test_metric_uncertainty_keeps_dependent_score_unknown_after_new_proposals(tmp_path):
    app,c=setup(tmp_path)
    assert submit(c,target_type='metric',target_id='m2_2023').status_code==200
    # A subsequent proposal must not turn a pending boundary question into full coverage.
    with app.state.tasks.transaction() as db:
        s=read_business(db,'t')
        for check in s['checks']:
            check.update(status='met',review_state='confirmed',is_current=True)
        save_business(db,'t',s)
    from esg_app.review_dispositions import effective_checks,restricted_check_ids
    from esg_app.scoring import score_checks
    s=state(app);blocked=restricted_check_ids(s)
    assert blocked
    result=score_checks(effective_checks(s,s['checks']),app.state.rulebook)
    assert result['final_score'] is None and float(result['coverage'])<1
    assert submit(c,expected_revision=1,target_id=next(iter(blocked)),decision='confirm').status_code==422


@pytest.mark.parametrize('scope,key', [('root','owner'),('task','owner'),('task','user_id')])
def test_declared_task_owner_protects_read_and_write(tmp_path,scope,key):
    app,c=setup(tmp_path)
    with app.state.tasks.transaction() as db:
        metadata=json.loads(db.execute('SELECT results FROM tasks WHERE task_id=?',('t',)).fetchone()[0])
        target=metadata if scope=='root' else metadata['task']
        target[key]='another-user'
        db.execute('UPDATE tasks SET results=? WHERE task_id=?',(json.dumps(metadata),'t'))
    assert submit(c).status_code==403
    assert c.get('/tasks/t/review-dispositions',headers={'X-User':'reviewer'}).status_code==403
    assert c.get('/tasks/t/review-dispositions',headers={'X-User':'another-user'}).status_code==200


def test_disposition_text_is_bounded_and_html_escaped(tmp_path):
    app,c=setup(tmp_path)
    assert submit(c,decision='confirm',search_scope='x'*4001).status_code==422
    assert submit(c,reason='<script>alert(1)</script>').status_code==200
    html=c.get('/?task_id=t').text
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
