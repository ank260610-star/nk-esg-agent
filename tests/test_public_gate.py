"""Gateway identity and cross-account boundaries; no real authentication secrets."""
from dataclasses import replace
import json
import pytest
from fastapi.testclient import TestClient
from esg_app.config import Settings
from esg_app.main import create_app
from esg_app.public_gate import owned

ORIGIN='https://beta.example.test'
SECRET='synthetic-test-secret-32-characters-only'


@pytest.fixture
def secured(tmp_path):
    app=create_app(Settings(public_mode=True, public_origin=ORIGIN, proxy_secret=SECRET,
                            database_path=str(tmp_path/'security.sqlite3')))
    for tid,owner in [('alice_task','alice'),('bob_task','bob'),('unowned',None)]:
        metadata={'task':{'task_id':tid,'company_id':owner,'report_ids':[tid+'_report'],'selected_years':[2023,2024]},'reports':[]}
        if owner:metadata['owner']=owner
        app.state.tasks.register_task(tid,0,metadata)
        p=tmp_path/(tid+'.pdf');p.write_bytes(b'%PDF synthetic')
        app.state.tasks.register_report_path(tid+'_report',p)
    app.state.artifacts.add('bob_task','json','application/json','secret.json',b'not alice data',0)
    return app,TestClient(app,base_url=ORIGIN)


def headers(user='alice',**kw):
    return {'X-ESG-Proxy-Secret':SECRET,'X-ESG-User':user,**kw}


def test_client_identity_and_cookie_cannot_authenticate(secured):
    _,c=secured
    assert c.get('/',headers={'X-User':'alice','Cookie':'esg_user=alice'}).status_code==401
    assert c.get('/',headers={'X-ESG-User':'alice','X-ESG-Proxy-Secret':'wrong'}).status_code==401


def test_default_task_and_response_headers(secured):
    _,c=secured;r=c.get('/',headers=headers())
    assert r.status_code==200 and 'alice_task' in r.text and 'bob_task' not in r.text
    assert 'set-cookie' not in r.headers and r.headers['cache-control']=='no-store'
    assert r.headers['x-content-type-options']=='nosniff'


@pytest.mark.parametrize('path',['/?task_id=bob_task','/?task_id=unowned','/api/reports/bob_task_report/pdf',
                                 '/tasks/bob_task/metric-reviews','/tasks/bob_task/agent-runs/missing'])
def test_foreign_and_unowned_resources_are_hidden(secured,path):
    _,c=secured;assert c.get(path,headers=headers()).status_code==404


def test_pdf_and_export_download_ownership(secured):
    app,c=secured
    assert c.get('/api/reports/alice_task_report/pdf',headers=headers()).status_code==200
    aid=app.state.artifacts.list_for_task('bob_task')[0]['artifact_id']
    assert c.get('/api/artifacts/'+aid,headers=headers()).status_code==404
    assert c.get('/api/artifacts/'+aid,headers=headers('bob')).status_code==200


def test_spoofed_user_header_does_not_change_gateway_identity(secured):
    _,c=secured
    r=c.get('/?task_id=bob_task',headers=headers(**{'X-User':'bob','Cookie':'esg_user=bob'}))
    assert r.status_code==404
    assert c.get('/tasks/alice_task/metric-reviews',headers=headers(**{'X-User':'bob'})).status_code==200


def test_write_origin_and_chunked_size_limits(secured):
    app,c=secured
    path='/tasks/alice_task/metric-reviews'
    assert c.post(path,headers=headers(),json={}).status_code==403
    assert c.post(path,headers=headers(Origin='https://evil.example'),json={}).status_code==403
    r=c.post(path,headers=headers(Origin=ORIGIN),content=iter([b'a'*600000,b'b'*600000]))
    assert r.status_code==413
    r=c.post(path,headers=headers(Origin=ORIGIN),json={})
    assert r.status_code==400  # Reaches authenticated business validation, not origin gate.


def test_model_budget_is_zero_even_for_valid_agent_token(secured):
    app,c=secured;app.state.tasks.issue_agent_token('alice_task','valid-test-token')
    for path in ['/agent/tool','/api/tasks/alice_task/extract','/tasks/alice_task/agent-runs']:
        r=c.post(path,headers=headers(Origin=ORIGIN,**{'X-Agent-Token':'valid-test-token'}),json={})
        assert r.status_code==403 and r.json()['error']['code']=='MODEL_BUDGET_ZERO'


def test_rate_limit_survives_app_restart(tmp_path):
    s=Settings(public_mode=True,public_origin=ORIGIN,proxy_secret=SECRET,requests_per_minute=1,database_path=str(tmp_path/'rate.sqlite3'))
    c=TestClient(create_app(s),base_url=ORIGIN)
    assert c.get('/',headers=headers()).status_code==404
    restarted=TestClient(create_app(s),base_url=ORIGIN)
    assert restarted.get('/',headers=headers()).status_code==429


@pytest.mark.parametrize('patch',[{'public_origin':'http://bad'}, {'proxy_secret':'short'}, {'harness_enabled':True},
                                  {'extraction_model':'any'}, {'database_path':':memory:'}])
def test_public_mode_fails_closed_on_unsafe_configuration(tmp_path,patch):
    s=Settings(public_mode=True,public_origin=ORIGIN,proxy_secret=SECRET,database_path=str(tmp_path/'bad.sqlite3'))
    with pytest.raises(ValueError):create_app(replace(s,**patch))


def test_declared_owners_must_all_match():
    assert not owned({},'alice')
    assert not owned({'owner':'alice','task':{'user_id':'bob'}},'alice')


def test_public_portable_artifact_rejects_symlink_escape(secured,tmp_path):
    app,c=secured
    artifact=app.state.artifacts.add('alice_task','json','application/json','owned.json',b'owned',0)
    meta=app.state.artifacts.get_meta(artifact['artifact_id'])
    from pathlib import Path
    p=Path(meta['_path']);p.unlink()
    external=tmp_path/'outside.json';external.write_text('outside content')
    p.symlink_to(external)
    assert c.get('/api/artifacts/'+artifact['artifact_id'],headers=headers()).status_code==404
