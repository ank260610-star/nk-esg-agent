"""Owner-bound business materialization and frontend round trip with synthetic data."""
import hashlib
import json
import sqlite3
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from esg_app import beta_workspace
from esg_app.config import Settings
from esg_app.main import create_app
from test_public_gate import ORIGIN, SECRET, headers


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    report='BYD-2024-synthetic';pdf=b'%PDF synthetic, not real report'
    package=tmp_path/'package.zip'
    with zipfile.ZipFile(package,'w')as z:z.writestr('synthetic.pdf',pdf)
    obs=[];evidence=[]
    for name,metric in beta_workspace.METRICS.items():
        for year in ('2023','2024'):
            eid=f'{metric}_{year}';missing=metric=='E03'and year=='2023'
            pending=metric=='E02'or missing
            obs.append(dict(company='BYD',import_batch='missing_marker'if missing else 'quarantine_pending'if pending else 'final_candidate',
                            report_id=report,standard_indicator=name,year=year,raw_value='/'if missing else '100',value=''if missing else'100',
                            unit_raw='tCO2e',status='missing_marker'if missing else 'disclosed',evidence_id=eid,
                            caveat='synthetic pending'if pending else'',related_pend='PEND-TEST'if pending else'',
                            missing_materials='synthetic missing materials',pending_status='pending'if pending else'none'))
            evidence.append(dict(evidence_id=eid,report_id=report,physical_pages=[1],text='2023 2024 100 tCO2e / synthetic text'))
    rule=json.loads(open('backend/src/esg_app/rules/disclosure-v0.1.json').read())
    checks=[dict(company='BYD',check_id=c['check_id'],evidence_id='E01_2024',标准结论='met',判定理由='synthetic proposal')for c in rule['checks']]
    data=dict(observations=obs,evidence=evidence,checks=checks,package_sha256='0'*64,rule_version='disclosure-v0.1',
              reports=[dict(report_id=report,年度='2024',页数='1',是否入包='是',**{'SHA-256':hashlib.sha256(pdf).hexdigest()})])
    monkeypatch.setattr(beta_workspace,'adapt',lambda _:data)
    out=tmp_path/'business'
    result=beta_workspace.prepare_workspace(package,out,'alice')
    return out,result


def test_beta_frontend_review_export_and_restart(prepared):
    out,result=prepared
    assert result['records']==12 and result['automatically_confirmed']==0 and not result['test_answers_imported']
    settings=Settings(public_mode=True,public_origin=ORIGIN,proxy_secret=SECRET,database_path=str(out/'business.sqlite3'))
    c=TestClient(create_app(settings),base_url=ORIGIN)
    page=c.get('/',headers=headers())
    assert page.status_code==200 and 'byd_v4_review' in page.text
    assert 'synthetic pending' in page.text
    assert c.get('/',headers=headers('bob')).status_code==404
    assert c.get('/api/reports/BYD-2024-synthetic/pdf',headers=headers()).status_code==200
    pending=c.post('/tasks/byd_v4_review/review-dispositions',headers=headers(Origin=ORIGIN),json=dict(
        expected_revision=0,target_type='metric',target_id='import_E02_2023',decision='confirm',reason='attempted bypass'))
    assert pending.status_code==422
    exported=c.post('/tasks/byd_v4_review/exports',headers=headers(Origin=ORIGIN,**{'Idempotency-Key':str(uuid4())}),json={'expected_revision':0})
    assert exported.status_code==200,exported.text
    app=create_app(settings);restarted=TestClient(app,base_url=ORIGIN)
    artifacts=app.state.artifacts.list_for_task('byd_v4_review')
    assert len(artifacts)==3
    for a in artifacts:
        response=restarted.get('/api/artifacts/'+a['artifact_id'],headers=headers())
        assert response.status_code==200
        assert restarted.get('/api/artifacts/'+a['artifact_id'],headers=headers('bob')).status_code==404
        if a['format']=='json':
            payload=response.json()
            assert payload['score']['final_score']is None
            assert all(r['review_state']=='unreviewed'for r in payload['metrics'])
            assert all(c['status']=='unknown'for c in payload['checks'])
            assert payload['review_dispositions']
    # Saving one check through the legacy frontend form cannot silently remove a metric limitation.
    response=restarted.post('/tasks/byd_v4_review/review',headers=headers(Origin=ORIGIN),json=dict(
        expected_revision=0,check_id='C01',new_status='met',reason='synthetic manual review'))
    assert response.status_code==200,response.text
    with sqlite3.connect(out/'business.sqlite3')as db:
        raw=db.execute('SELECT data FROM business_state').fetchone()[0]
        assert 'system_import'in raw and 'PEND-TEST'in raw


def test_existing_destination_is_never_overwritten(prepared,tmp_path,monkeypatch):
    out,_=prepared
    with pytest.raises(FileExistsError):beta_workspace.prepare_workspace(tmp_path/'package.zip',out,'alice')


def test_business_copy_can_move_to_a_deployment_mount(prepared,tmp_path):
    import shutil
    out,_=prepared
    s=Settings(public_mode=True,public_origin=ORIGIN,proxy_secret=SECRET,database_path=str(out/'business.sqlite3'))
    app=create_app(s);c=TestClient(app,base_url=ORIGIN)
    r=c.post('/tasks/byd_v4_review/exports',headers=headers(Origin=ORIGIN,**{'Idempotency-Key':str(uuid4())}),json={'expected_revision':0})
    assert r.status_code==200
    app.state.tasks.db.close()
    moved=tmp_path/'deployed';shutil.copytree(out,moved)
    relocated=TestClient(create_app(Settings(public_mode=True,public_origin=ORIGIN,proxy_secret=SECRET,
                                           database_path=str(moved/'business.sqlite3'))),base_url=ORIGIN)
    assert relocated.get('/api/reports/BYD-2024-synthetic/pdf',headers=headers()).status_code==200
    with sqlite3.connect(moved/'business.sqlite3')as db:
        aid=db.execute('SELECT artifact_id FROM artifacts LIMIT 1').fetchone()[0]
    assert relocated.get('/api/artifacts/'+aid,headers=headers()).status_code==200


def test_preflight_matches_gateway_account_to_task_owner(prepared,tmp_path):
    import importlib.util
    out,_=prepared
    spec=importlib.util.spec_from_file_location('preflight','scripts/preflight_beta.py')
    preflight=importlib.util.module_from_spec(spec);spec.loader.exec_module(preflight)
    env=tmp_path/'beta.env'
    content=f'ESG_PUBLIC_HOST=beta.example.test\nESG_PROXY_SECRET={SECRET}\nESG_REVIEWER_USER=alice\nESG_REVIEWER_PASSWORD_HASH=$2b$12$'+('a'*53)+f'\nESG_BETA_DATA_DIR={out}\n'
    env.write_text(content);env.chmod(0o600)
    assert preflight.check(env)['model_budget']==0
    env.write_text(content.replace('ESG_REVIEWER_USER=alice','ESG_REVIEWER_USER=bob'))
    with pytest.raises(ValueError,match='owner'):preflight.check(env)
    env.chmod(0o644)
    with pytest.raises(ValueError,match='private'):preflight.check(env)
