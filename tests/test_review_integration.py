"""Cross-interface regressions for PR #9 plus PR #10; no provider calls."""
from uuid import uuid4

from esg_app.parse_repository import read_business, save_business
from esg_app.review_dispositions import current_dispositions, review_complete, restricted_check_ids
from test_review_dispositions import setup, submit, state


def metric_request(c, record_id, revision, action='confirm', correction=None):
    body=dict(action=action, record_id=record_id, expected_revision=revision,
              reason='核对原文数值，不代表已解决范围或因子问题',evidence_ids=['ev1'])
    if correction is not None: body['correction']=correction
    return c.post('/tasks/t/metric-reviews',headers={'X-User':'reviewer','Idempotency-Key':str(uuid4())},json=body)


def test_metric_confirm_does_not_resolve_pending_scope(tmp_path):
    app,c=setup(tmp_path)
    assert submit(c,target_type='metric',target_id='m1_2023').status_code==200
    assert metric_request(c,'m1_2023',1).status_code==200
    s=state(app);r=next(r for r in s['records'] if r['record_id']=='m1_2023')
    assert not review_complete(s,r,'metric')
    assert restricted_check_ids(s)


def test_manual_successor_keeps_pending_limitation(tmp_path):
    app,c=setup(tmp_path)
    assert submit(c,target_type='metric',target_id='m1_2023').status_code==200
    response=metric_request(c,'m1_2023',1,'correct',{'extraction_note':'仅补充数值来源，边界尚待企业确认'})
    assert response.status_code==200,response.text
    r=response.json()['data']['record']
    assert r['supersedes_id']=='m1_2023'
    assert not review_complete(state(app),r,'metric')
    assert restricted_check_ids(state(app))


def test_old_check_review_does_not_silently_clear_unknown_case(tmp_path):
    app,c=setup(tmp_path);assert submit(c).status_code==200
    cid=app.state.rulebook['checks'][0]['check_id']
    response=c.post('/tasks/t/review',headers={'X-User':'reviewer'},json=dict(
        expected_revision=1,check_id=cid,new_status='met',reason='只复核该项建议'))
    assert response.status_code==200
    check=next(x for x in state(app)['checks'] if x['check_id']==cid)
    assert not review_complete(state(app),check,'check')
    assert cid in restricted_check_ids(state(app))


def test_disposition_confirm_cannot_bypass_evidence_support(tmp_path):
    app,c=setup(tmp_path)
    with app.state.tasks.transaction() as db:
        s=read_business(db,'t');s['records'][0].update(raw_value='999',value='999',review_state='unreviewed')
        save_business(db,'t',s)
    assert submit(c,target_type='metric',target_id='m1_2023',decision='confirm').status_code==422


def test_disposition_confirm_rejects_wrong_evidence_year(tmp_path):
    from esg_app.retrieval import Evidence,EvidenceStore
    app,c=setup(tmp_path)
    app.state.evidence=EvidenceStore([Evidence('ev1','r1',127,None,'table','100 吨二氧化碳当量',column_year=2024)])
    assert submit(c,target_type='metric',target_id='m1_2023',decision='confirm').status_code==422


def test_metric_review_rejects_active_run(tmp_path):
    app,c=setup(tmp_path);app.state.runs.start('t','busy_session')
    assert metric_request(c,'m1_2023',0).status_code==409


def test_resolution_requires_new_evidence_and_explicit_action(tmp_path):
    from esg_app.retrieval import Evidence,EvidenceStore
    app,c=setup(tmp_path)
    assert submit(c,target_type='metric',target_id='m1_2023').status_code==200
    assert submit(c,expected_revision=1,target_type='metric',target_id='m1_2023',decision='confirm').status_code==422
    assert submit(c,expected_revision=1,target_type='metric',target_id='m1_2023',decision='resolve',resolution_evidence_ids=['ev1']).status_code==422
    assert submit(c,expected_revision=1,target_type='metric',target_id='m1_2023',decision='resolve',resolution_evidence_ids=['foreign']).status_code==422
    app.state.evidence=EvidenceStore([Evidence('ev1','r1',127,None,'table','2023 100 tCO2e'),
                                     Evidence('ev2','r1',128,None,'paragraph','企业补证：边界与营收一致')])
    response=submit(c,expected_revision=1,target_type='metric',target_id='m1_2023',decision='resolve',resolution_evidence_ids=['ev2'])
    assert response.status_code==200,response.text
    s=state(app);r=next(x for x in s['records'] if x['record_id']=='m1_2023')
    assert review_complete(s,r,'metric') and not restricted_check_ids(s)
    assert response.json()['data']['resolution_evidence_ids']==['ev2']
    assert response.json()['data']['evidence_refs'][-1]['pdf_page']==128


def test_successor_limitations_remain_in_page_and_all_exports(tmp_path):
    import csv,io,json
    from esg_app.review_dispositions import visible_dispositions
    app,c=setup(tmp_path)
    assert submit(c,target_type='metric',target_id='m1_2023').status_code==200
    response=metric_request(c,'m1_2023',1,'correct',{'extraction_note':'补充来源'})
    assert response.status_code==200
    rid=response.json()['data']['record']['record_id']
    visible=visible_dispositions(state(app))
    assert visible[0]['target_id']==rid and visible[0]['needs_recheck']
    html=c.get('/?task_id=t').text
    assert '原处置已失效，限制仍保留，需重新核查' in html
    assert '不得将反推比值' in html
    response=c.post('/tasks/t/exports',headers={'X-User':'reviewer','Idempotency-Key':str(uuid4())},json={'expected_revision':2})
    assert response.status_code==200,response.text
    for a in app.state.artifacts.list_for_task('t'):
        raw=app.state.artifacts.get_content(a['artifact_id']).decode()
        if a['format']=='json':
            data=json.loads(raw)
            assert data['review_dispositions'][0]['needs_recheck']
            assert data['score']['final_score'] is None
        elif a['format']=='markdown':assert '不得将反推比值' in raw and 'needs_recheck' in raw
        else:
            row=next(r for r in csv.DictReader(io.StringIO(raw)) if r['review_target_id']==rid)
            assert row['review_needs_recheck']=='True' and row['use_limitations']
    # Human can re-acknowledge insufficient evidence on the successor, without inventing a fact.
    assert submit(c,expected_revision=2,target_type='metric',target_id=rid).status_code==200
    s=state(app);r=next(x for x in s['records'] if x['record_id']==rid)
    assert review_complete(s,r,'metric') and restricted_check_ids(s)


def test_whitespace_separates_evidence_numbers_and_markers_are_not_unit_slashes(tmp_path):
    from esg_app.metric_review import _evidence_supports
    from esg_app.retrieval import Evidence
    app,c=setup(tmp_path);r=state(app)['records'][0]
    e=Evidence('ev1','r1',127,None,'table','2023 2024 100 tCO2e')
    assert _evidence_supports(e,r)
    assert not _evidence_supports(e,dict(r,value='20232024100',raw_value='20232024100'))
    assert not _evidence_supports(Evidence('ev1','r1',127,None,'table','0.1 tCO2e/万元营收'),dict(r,status='missing_marker',raw_value='/',value=None))
    assert not _evidence_supports(e,dict(r,raw_value='0.1',value='100',conversion_rule='乘1000'))


def test_metric_replay_survives_active_run(tmp_path):
    app,c=setup(tmp_path)
    key=str(uuid4());body=dict(action='confirm',record_id='m1_2023',expected_revision=0,reason='核对原文',evidence_ids=['ev1'])
    headers={'X-User':'reviewer','Idempotency-Key':key}
    first=c.post('/tasks/t/metric-reviews',headers=headers,json=body)
    assert first.status_code==200
    app.state.runs.start('t','busy_session')
    replay=c.post('/tasks/t/metric-reviews',headers=headers,json=body)
    assert replay.status_code==200 and replay.json()==first.json()


def test_both_routes_reject_conflicting_declared_owners(tmp_path):
    import json
    app,c=setup(tmp_path)
    with app.state.tasks.transaction() as db:
        metadata=json.loads(db.execute('SELECT results FROM tasks WHERE task_id=?',('t',)).fetchone()[0])
        metadata['task']['owner']='reviewer';metadata['user_id']='someone-else'
        db.execute('UPDATE tasks SET results=? WHERE task_id=?',(json.dumps(metadata),'t'))
    assert metric_request(c,'m1_2023',0).status_code==403
    assert c.get('/tasks/t/metric-reviews',headers={'X-User':'reviewer'}).status_code==403
    assert submit(c).status_code==403


def test_evidence_aliases_preserve_compound_denominator_and_scale():
    from esg_app.metric_review import _evidence_supports
    from esg_app.retrieval import Evidence
    from test_metrics import candidate
    r=candidate(metric_id='E02',year=2023,report_id='r1',evidence_ids=['ev1'])
    r.update(raw_value='0.2049',value='0.2049',raw_unit='吨二氧化碳当量/万元营收',canonical_unit='tCO2e/万元营收')
    assert _evidence_supports(Evidence('ev1','r1',127,None,'table','2023 0.2049 tCO2e/万元营收'),r)
    assert not _evidence_supports(Evidence('ev1','r1',127,None,'table','2023 0.2049 tCO2e/亿元营收'),r)
    r.update(metric_id='E01',raw_value='100',value='100',raw_unit='tCO2e',canonical_unit='tCO2e')
    assert not _evidence_supports(Evidence('ev1','r1',127,None,'table','2023 100 万tCO2e'),r)
    assert not _evidence_supports(Evidence('ev1','r1',127,None,'table','2023 100 tCO2e/万元营收'),r)
