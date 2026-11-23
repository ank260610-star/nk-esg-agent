"""服务端契约与真实 PDF 库的合成文档验证；不是企业参考答案。"""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from esg_app.config import Settings
from esg_app.main import create_app
from esg_app.parse_repository import ParseRepository
from esg_app.parse_service import ParseError, ParseService, parse_pdf

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'contracts/schema.json').read_text())


def pdf_bytes(text='Scope 1 emissions 123.40', blank=False):
    out = io.BytesIO()
    if blank:
        pdf = PdfWriter()
        pdf.add_blank_page(200, 200)
        pdf.write(out)
    else:
        pdf = canvas.Canvas(out)
        pdf.drawString(30, 100, text)
        pdf.save()
    return out.getvalue()


def setup(tmp_path, data=None):
    app = create_app(Settings(database_path=str(tmp_path / 'db.sqlite3')))
    repo = app.state.tasks
    repo.register_task('t', 6, {'report_ids': ['r']})
    repo.issue_agent_token('t', 'secret')
    path = tmp_path / 'report.pdf'
    path.write_bytes(data if data is not None else pdf_bytes())
    repo.register_report_path('r', path)
    return app, TestClient(app), repo


def request(client, revision=6, key=None, token='secret', task='t', body=None):
    return client.post(f'/api/tasks/{task}/parse', json=body if body is not None else {'expected_revision': revision},
        headers={'Authorization': f'Bearer {token}', 'Idempotency-Key': key or str(uuid4())})


def validate(response):
    Draft202012Validator({**SCHEMA, '$ref': '#/$defs/parse_report_output'}).validate(response.json())


def test_first_parse_replay_cache_and_restart(tmp_path):
    app, client, repo = setup(tmp_path)
    key = str(uuid4())
    first = request(client, key=key)
    assert first.status_code == 200, first.text
    validate(first)
    assert first.json()['revision'] == 7
    evidence = repo.evidence_for_task('t')
    assert evidence and '123.40' in evidence[0]['text']
    assert request(client, key=key).json() == first.json()
    conflict = request(client, revision=7, key=key)
    assert conflict.status_code == 409
    assert conflict.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    repo.get('t').revision = 8
    assert request(client, key=key).json() == first.json()
    assert request(client, revision=8).json()['revision'] == 8
    assert repo.evidence_for_task('t') == evidence
    app2 = create_app(Settings(database_path=str(tmp_path / 'db.sqlite3')))
    client2 = TestClient(app2)
    assert request(client2, key=key).json() == first.json()
    assert app2.state.tasks.evidence_for_task('t') == evidence
    assert request(client2, revision=8).json()['revision'] == 8
    # 解析摘要不夹带进旧 get_task_results 的数据结构。
    assert repo.get('t').results == {'report_ids':['r']}


@pytest.mark.parametrize('body', [{}, [], {'expected_revision': True}, {'expected_revision': -1},
    {'expected_revision': '6'}, {'expected_revision': 6, 'path': '/etc/passwd'}])
def test_invalid_inputs(tmp_path, body):
    _, client, repo = setup(tmp_path)
    response = request(client, body=body)
    assert response.status_code == 422
    validate(response)
    assert repo.get('t').revision == 6


def test_auth_missing_key_and_stale_revision(tmp_path):
    _, client, repo = setup(tmp_path)
    for token, task, code in [('bad', 't', 401), ('secret', 'other', 403)]:
        response = request(client, token=token, task=task)
        assert response.status_code == code
        assert response.json()['task_id'] is None
        assert response.json()['revision'] is None
    response = client.post('/api/tasks/t/parse', json={'expected_revision': 6}, headers={'Authorization': 'Bearer secret'})
    assert response.status_code == 422
    response = request(client, revision=5)
    assert response.status_code == 409
    validate(response)
    assert not repo.evidence_for_task('t')


@pytest.mark.parametrize('data,code', [(b'bad pdf', 'INVALID_PDF'), (pdf_bytes(blank=True), 'NO_TEXT_LAYER')])
def test_failures_do_not_consume_key(tmp_path, data, code):
    _, client, repo = setup(tmp_path, data)
    key = str(uuid4())
    response = request(client, key=key)
    assert response.status_code == 422
    assert response.json()['error']['code'] == code
    validate(response)
    assert repo.get('t').revision == 6
    assert not repo.evidence_for_task('t')
    repo.report_path('r').write_bytes(pdf_bytes())
    assert request(client, key=key).status_code == 200


def test_partial_pages_and_schema(tmp_path):
    out = io.BytesIO()
    pdf = canvas.Canvas(out)
    pdf.drawString(30, 100, 'test')
    pdf.showPage()
    pdf.showPage()
    pdf.save()
    _, client, _ = setup(tmp_path, out.getvalue())
    response = request(client)
    assert response.status_code == 200
    validate(response)
    assert response.json()['data']['status'] == 'partial'
    assert response.json()['data']['reports'][0]['parsed_pages'] == 1


def test_parse_time_revision_change_rejects_all_writes(tmp_path):
    app, client, repo = setup(tmp_path)
    def parser(data):
        repo.get('t').revision = 7
        return parse_pdf(data)
    app.state.parse_service.parser = parser
    response = request(client)
    assert response.status_code == 409
    assert repo.evidence_for_task('t') == []
    with repo.transaction() as db:
        assert db.execute('SELECT count(*) FROM idempotency').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM parse_cache').fetchone()[0] == 0


@pytest.mark.parametrize('same_key', [True, False])
def test_concurrent_commits_across_connections(tmp_path, same_key):
    _, _, repo = setup(tmp_path)
    other = ParseRepository(tmp_path / 'db.sqlite3')
    gate = Barrier(2)
    def parser(data):
        gate.wait(timeout=10)
        return parse_pdf(data)
    services = [ParseService(repo, {}, parser), ParseService(other, {}, parser)]
    key = str(uuid4())
    def run(index):
        try:
            return services[index].run('t', 'secret', key if same_key else str(uuid4()), {'expected_revision': 6})
        except ParseError as error:
            return error.payload
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(r['ok'] for r in results) == (2 if same_key else 1)
    if same_key:
        assert results[0] == results[1]
    else:
        assert [r['error']['code'] for r in results if not r['ok']] == ['REVISION_CONFLICT']
    assert repo.get('t').revision == 7
    assert len(repo.evidence_for_task('t')) == 1
    other.close()


def test_transaction_failure_rolls_back_and_key_reusable(tmp_path):
    _, client, repo = setup(tmp_path)
    with repo.transaction() as db:
        db.execute("CREATE TRIGGER fail_commit BEFORE INSERT ON idempotency BEGIN SELECT RAISE(ABORT, 'test'); END")
    key = str(uuid4())
    response = request(client, key=key)
    assert response.status_code == 500
    assert 'test' not in response.text
    assert repo.get('t').revision == 6
    assert not repo.evidence_for_task('t')
    with repo.transaction() as db:
        assert db.execute('SELECT count(*) FROM parse_cache').fetchone()[0] == 0
        db.execute('DROP TRIGGER fail_commit')
    assert request(client, key=key).status_code == 200


def test_agent_entry_and_task_evidence_isolation(tmp_path):
    _, client, repo = setup(tmp_path)
    key = str(uuid4())
    response = client.post('/agent/tool', headers={'X-Agent-Token':'secret', 'Idempotency-Key':key},
        json={'tool':'parse_report','arguments':{'expected_revision':6}})
    assert response.status_code == 200
    validate(response)
    assert request(client, key=key).json() == response.json()
    repo.register_task('other', 0, {'report_ids': ['different']})
    assert repo.evidence_for_task('other') == []
    search = client.post('/agent/tool', headers={'X-Agent-Token':'secret'},
        json={'tool':'search_evidence','arguments':{'query':'123.40'}})
    assert search.status_code == 200
    assert search.json()['data']['evidence']


def test_parser_version_replaces_evidence_and_increments_once(tmp_path):
    app, client, repo = setup(tmp_path)
    assert request(client).status_code == 200
    old = repo.evidence_for_task('t')
    app.state.parse_service.version = 'coord-v2'
    assert request(client, revision=7).json()['revision'] == 8
    new = repo.evidence_for_task('t')
    assert len(new) == len(old)
    assert new[0]['evidence_id'] != old[0]['evidence_id']
    assert request(client, revision=8).json()['revision'] == 8


def test_two_reports_partial_and_cache_association(tmp_path):
    app, client, repo = setup(tmp_path)
    blank = tmp_path / 'blank.pdf'
    blank.write_bytes(pdf_bytes(blank=True))
    repo.register_report_path('blank', blank)
    with repo.transaction() as db:
        db.execute('UPDATE tasks SET results=? WHERE task_id=?', (json.dumps({'report_ids':['r','blank']}), 't'))
    response = request(client)
    assert response.status_code == 200
    validate(response)
    assert response.json()['data']['status'] == 'partial'
    assert [s['status'] for s in response.json()['data']['reports']] == ['parsed', 'failed']
    repo.register_task('second', 0, {'report_ids':['r']})
    repo.issue_agent_token('second','second-secret')
    app.state.parse_service.parser = lambda data: pytest.fail('valid cache should be reused')
    response = request(client, task='second', token='second-secret', revision=0)
    assert response.status_code == 200
    assert response.json()['revision'] == 1
    assert repo.evidence_for_task('second')


def test_pdf_limits_and_encryption(tmp_path, monkeypatch):
    from esg_app import parse_service
    _, client, repo = setup(tmp_path)
    monkeypatch.setattr(parse_service, 'MAX_BYTES', 5)
    response = request(client)
    assert response.status_code == 413
    assert response.json()['error']['code'] == 'FILE_TOO_LARGE'
    monkeypatch.setattr(parse_service, 'MAX_BYTES', 50*1024*1024)
    out = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(200,200)
    writer.encrypt('password')
    writer.write(out)
    repo.report_path('r').write_bytes(out.getvalue())
    response = request(client)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'ENCRYPTED_PDF'
    assert repo.get('t').revision == 6


def test_schema_limits_and_page_cap(tmp_path):
    app, client, repo = setup(tmp_path)
    app.state.parse_service.parser = lambda _: {'page_count': 300, 'pages':[
        {'number':1,'text':'x'*40001,'rows':[], 'issue':None},
        *[{'number':i,'text':'','rows':[],'issue':'PAGE_PARSE_FAILED'} for i in range(2,301)]]}
    response = request(client)
    assert response.status_code == 200
    validate(response)
    for evidence in repo.evidence_for_task('t'):
        Draft202012Validator({**SCHEMA,'$ref':'#/$defs/Evidence'}).validate(evidence)
    out = io.BytesIO()
    writer = PdfWriter()
    for _ in range(301):
        writer.add_blank_page(200,200)
    writer.write(out)
    with pytest.raises(ParseError) as exc:
        parse_pdf(out.getvalue())
    assert exc.value.payload['error']['code'] == 'PAGE_LIMIT'


def test_binding_change_during_parse_rejects_commit(tmp_path):
    app, client, repo = setup(tmp_path)
    def parser(data):
        with repo.transaction() as db:
            db.execute('UPDATE tasks SET results=? WHERE task_id=?', (json.dumps({'report_ids':['changed']}), 't'))
        return parse_pdf(data)
    app.state.parse_service.parser = parser
    response = request(client)
    assert response.status_code == 409
    assert not repo.evidence_for_task('t')


def test_revoked_token_during_parse_rejects_commit(tmp_path):
    app, client, repo = setup(tmp_path)
    def parser(data):
        with repo.transaction() as db:
            db.execute('DELETE FROM tokens')
        return parse_pdf(data)
    app.state.parse_service.parser = parser
    response = request(client)
    assert response.status_code == 403
    assert response.json()['task_id'] is None
    assert repo.get('t').revision == 6
    assert not repo.evidence_for_task('t')


def test_table_failure_keeps_text(tmp_path, monkeypatch):
    from esg_app import parse_service
    _, client, repo = setup(tmp_path)
    def broken(*args, **kwargs):
        raise ValueError('internal detail must not be exposed')
    monkeypatch.setattr(parse_service, 'reconstruct_table', broken)
    response = request(client)
    assert response.status_code == 200
    validate(response)
    assert response.json()['data']['status'] == 'partial'
    assert 'TABLE_PARSE_FAILED' in response.text
    assert 'internal detail' not in response.text
    assert '123.40' in repo.evidence_for_task('t')[0]['text']


def test_malformed_json_requires_authorization(tmp_path):
    _, client, _ = setup(tmp_path)
    response = client.post('/api/tasks/t/parse', content='{', headers={'Content-Type':'application/json'})
    assert response.status_code == 401
    response = client.post('/api/tasks/t/parse', content='{', headers={
        'Authorization':'Bearer secret','Idempotency-Key':str(uuid4()),'Content-Type':'application/json'})
    assert response.status_code == 422
    validate(response)


def test_state_map_report_path_survives_restart(tmp_path):
    app, client, repo = setup(tmp_path)
    path = repo.report_path('r')
    with repo.transaction() as db:
        db.execute('DELETE FROM reports')
    app.state.report_paths['r'] = path
    assert request(client).status_code == 200
    app2 = create_app(Settings(database_path=str(tmp_path / 'db.sqlite3')))
    assert app2.state.tasks.report_path('r') == path.resolve()
    c2 = TestClient(app2)
    c2.get('/')
    assert c2.get('/api/reports/r/pdf').status_code == 200


def test_missing_bound_file_has_no_path_leak(tmp_path):
    _, client, repo = setup(tmp_path)
    repo.report_path('r').unlink()
    response = request(client)
    assert response.status_code == 404
    validate(response)
    assert str(tmp_path) not in response.text
    assert repo.get('t').revision == 6


def test_review_uses_authoritative_revision_after_parse(tmp_path):
    _, client, repo = setup(tmp_path)
    def review(revision):
        return client.post('/tasks/t/review', headers={'X-User':'reviewer'}, json={
            'check_id':'C01','new_status':'partial','reason':'verify evidence','expected_revision':revision})
    assert review(6).status_code == 200
    assert request(client, revision=7).json()['revision'] == 8
    stale = review(7)
    assert stale.status_code == 409
    assert repo.get('t').revision == 8
    fresh = review(8)
    assert fresh.status_code == 200
    assert fresh.json()['revision'] == 9


def test_exports_follow_configured_database_directory(tmp_path):
    app, client, repo = setup(tmp_path)
    response = client.post('/tasks/t/exports', headers={'X-User':'reviewer'}, json={'expected_revision':6})
    assert response.status_code == 200, response.text
    for artifact in response.json()['data']['artifacts']:
        assert (tmp_path / 'artifacts' / artifact['filename']).is_file()
        assert client.get(artifact['download_url'], headers={'X-User':'reviewer'}).status_code == 200
