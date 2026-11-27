"""Prepare an owner-bound business copy from V4; never modify an existing database."""
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .import_v4 import adapt, require
from .main import create_app
from .model_extractor import unit_identity
from .parse_repository import save_business
from .review_dispositions import target_fingerprint
from .write_service import validate

METRICS = {'范围1+2温室气体排放量': 'E01', '范围1+2温室气体排放强度': 'E02',
           '范围3温室气体排放量': 'E03', '能源总耗量': 'E04', '总取水量': 'E05', '总耗水量': 'E06'}


def prepare_workspace(package, destination, owner):
    require(bool(re.fullmatch(r'[A-Za-z0-9@_.+-]{1,128}', owner)), 'Explicit gateway owner is required')
    data = adapt(package)
    selected = [r for r in data['observations'] if r['company'] == 'BYD'
                and r['import_batch'] in ('final_candidate', 'quarantine_pending', 'missing_marker')
                and r['standard_indicator'] in METRICS]
    require(len(selected) == 12 and len({(METRICS[r['standard_indicator']], r['year']) for r in selected}) == 12,
            'Expected six BYD metrics for two years without duplicates')
    now = datetime.now(timezone.utc).isoformat()
    task_id = 'byd_v4_review'
    reports = [r for r in data['reports'] if r['report_id'].startswith('BYD') and r['是否入包'] == '是']
    report_ids = {r['report_id'] for r in reports}
    evidence = [e for e in data['evidence'] if e['report_id'] in report_ids and e['physical_pages']]
    evidence_ids = {e['evidence_id'] for e in evidence}
    records, dispositions = [], []
    for r in selected:
        metric = METRICS[r['standard_indicator']]
        record = dict(report_id=r['report_id'], metric_id=metric, metric_year=int(r['year']),
                      raw_value=r['raw_value'], value=r['value'] or None, raw_unit=r['unit_raw'] or None,
                      canonical_unit=None if r['status'] == 'missing_marker' else unit_identity(r['unit_raw']),
                      conversion_rule=None, denominator=None, boundary_text='', boundary_key=None,
                      method_text='', scope3_categories=[], evidence_ids=[r['evidence_id']],
                      status='missing_marker' if r['status'] == 'missing_marker' else 'extracted', origin='reported',
                      supersedes_id=None, derivation=None, record_id=f'import_{metric}_{r["year"]}',
                      task_id=task_id, review_state='unreviewed', revision=0, is_current=True,
                      extraction_note='V4候选资料导入，尚未人工确认；' + r['caveat'])
        require(validate('MetricRecord', record), f'Invalid converted record: {metric}/{r["year"]}')
        require(r['evidence_id'] in evidence_ids, 'Record has unavailable evidence')
        records.append(record)
        if r['pending_status'] == 'pending':
            dispositions.append(dict(target_type='metric', target_id=record['record_id'], decision='reopen',
                                     reason='导入保留未解决事项：' + r['related_pend'],
                                     search_scope='V4资料包；未作新的人工核查', missing_material=r['missing_materials'],
                                     limitations=r['caveat'], actor='system_import', at=now, revision=0,
                                     evidence_ids=record['evidence_ids'], resolution_evidence_ids=[],
                                     previous_target=dict(record), fingerprint=target_fingerprint(record),
                                     evidence_refs=[], report_versions=[]))
    proposals = [r for r in data['checks'] if r['company'] == 'BYD']
    checks = [dict(check_id=r['check_id'], task_id=task_id, rule_version=data['rule_version'], status='unknown',
                   origin='manual', review_state='unreviewed', revision=0, is_current=True,
                   evidence_ids=[r['evidence_id']], reason='资料建议为' + r['标准结论'] + '，尚未人工复核；' + r['判定理由'],
                   missing_information=['需人工复核资料建议'], suggestion='对照报告核查后逐项确认', target_key=None)
              for r in proposals]
    require(all(r['evidence_ids'][0] in evidence_ids for r in checks), 'Check has unavailable evidence')
    require(all(validate('CheckResult', r) for r in checks), 'Invalid converted check')
    metadata = dict(owner=owner, task=dict(task_id=task_id, company_id='BYD', report_ids=sorted(report_ids),
                   selected_years=[2023, 2024], rule_version=data['rule_version'], stage='review', revision=0,
                   created_at=now, updated_at=now, error=None), reports=[])
    for r in reports:
        metadata['reports'].append(dict(report_id=r['report_id'], sha256=r['SHA-256'], company_id='BYD',
                                      company_name='比亚迪', report_year=int(r['年度']), published_date=None,
                                      source_url=None, kind='sustainability', media_type='application/pdf',
                                      page_count=int(r['页数']), parser_version='reference-package-v4', status='registered', issues=[]))
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    app = create_app(Settings(database_path=str(destination / 'business.sqlite3')))
    app.state.tasks.register_task(task_id, 0, metadata)
    with zipfile.ZipFile(package) as archive:
        pdfs = {hashlib.sha256(archive.read(n)).hexdigest(): n for n in archive.namelist() if n.endswith('.pdf')}
        for r in reports:
            path = destination / (r['report_id'] + '.pdf')
            path.write_bytes(archive.read(pdfs[r['SHA-256']]))
            app.state.tasks.register_report_path(r['report_id'], path)
    with app.state.tasks.transaction() as db:
        save_business(db, task_id, dict(records=records, checks=checks, comparisons=[], runs=[], issues=[],
                                       review_dispositions=dispositions, source_check_proposals=proposals,
                                       source_import_observations=[r for r in data['observations'] if r['company'] == 'BYD'],
                                       import_manifest=dict(sha256=data['package_sha256'], owner=owner,
                                                            source='V4', pending_not_resolved=True)))
        for r in reports:
            items = [dict(evidence_id=e['evidence_id'], report_id=e['report_id'], pdf_page=e['physical_pages'][0],
                          printed_page=None, kind='text', text=e['text']) for e in evidence if e['report_id'] == r['report_id']]
            summary = dict(page_count=int(r['页数']), status='partial', issues=[dict(code='REFERENCE_IMPORT',
                           message='资料摘录导入；尚未逐页核验PDF', report_id=r['report_id'], pdf_page=None, record_id=None)])
            db.execute('INSERT INTO task_parses VALUES(?,?,?,?,?,?)', (task_id, r['report_id'], r['SHA-256'],
                       'reference-package-v4', json.dumps(summary), json.dumps(items, ensure_ascii=False)))
    app.state.tasks.db.close()
    result = dict(task_id=task_id, owner=owner, records=len(records), checks=len(checks),
                  pending_records=len(dispositions), automatically_confirmed=0,
                  test_answers_imported=False, source_sha256=data['package_sha256'])
    (destination / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    for path in destination.iterdir():
        if path.is_file():
            path.chmod(0o600)
    return result
