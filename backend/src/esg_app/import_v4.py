"""V4 reference-package adapter. Writes only a new, isolated staging database."""
from __future__ import annotations
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from .model_extractor import unit_identity

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
BATCHES = {'final_candidate', 'quarantine_pending', 'missing_marker', 'reference_only', 'test_case'}


class ImportValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ImportValidationError(message)


def boolean(value):
    if value in ('1', 'true', 'TRUE'):
        return True
    if value in ('0', 'false', 'FALSE'):
        return False
    raise ImportValidationError(f'Invalid boolean: {value!r}')


def workbook(data):
    """Read values with physical row numbers; reject formulas instead of stale caches."""
    result = {'__warnings__': []}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        require(sum(i.file_size for i in z.infolist()) < 50_000_000, 'Workbook too large')
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        rels = {n.get('Id'): n.get('Target') for n in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets', NS):
            target = rels[sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            rows = []
            for row in ET.fromstring(z.read(path)).findall('.//s:sheetData/s:row', NS):
                cells = {}
                for c in row:
                    formula = c.find('s:f', NS)
                    if formula is not None:
                        require(sheet.get('name') == '指标明细' and c.get('r') in ('AE10', 'AE11')
                                and formula.text == '范围1+范围2；静态复算值', 'Formula cells require explicit materialization')
                        result['__warnings__'].append(f"{sheet.get('name')}!{c.get('r')}: explanation stored as formula; not executed")
                        cells[re.sub('[0-9]', '', c.get('r'))] = formula.text
                        continue
                    node = c.find('s:is', NS) if c.get('t') == 'inlineStr' else c.find('s:v', NS)
                    value = ''.join(node.itertext()) if node is not None else ''
                    if c.get('t') == 's' and value:
                        value = strings[int(value)]
                    cells[re.sub('[0-9]', '', c.get('r'))] = value
                if any(cells.values()):
                    rows.append((int(row.get('r')), cells))
            result[sheet.get('name')] = rows
    return result


def table(book, sheet, anchor):
    rows = book[sheet]
    header_index = next((i for i, (_, r) in enumerate(rows) if anchor in r.values()), None)
    require(header_index is not None, f'Missing header: {sheet}/{anchor}')
    headers = rows[header_index][1]
    return [dict({v: r.get(k, '') for k, v in headers.items() if v}, _row=row)
            for row, r in rows[header_index + 1:] if any(r.values())]


def validate_entries(observations, checks):
    for r in observations:
        require(r['company'] in ('BYD', 'GEELY'), 'Company not enabled for V4 import')
        if r['company'] == 'GEELY':
            require(r['import_batch'] == 'test_case', 'Test answers cannot become business candidates')
        require(r['import_batch'] in BATCHES, 'Unknown import_batch')
        usable, scoring = boolean(r['usable']), boolean(r['usable_for_scoring'])
        require(r['pending_status'] in ('none', 'pending'), 'Unknown pending_status')
        if r['import_batch'] == 'final_candidate':
            require(usable and scoring and r['pending_status'] == 'none', 'Pending/unusable final candidate')
            require(r['status'] in ('disclosed', 'derived') and r['unit_raw'] and r['unit_canonical'], 'Candidate lacks value/unit context')
            require(re.fullmatch(r'20[0-9]{2}', r['year']), 'Invalid candidate year')
            require(unit_identity(r['unit_raw']) == unit_identity(r['unit_canonical']), 'Unverified candidate unit conversion')
            try:
                require(Decimal(r['value']).is_finite(), 'Non-finite candidate value')
                require(Decimal(r['raw_value'].replace(',', '')) == Decimal(r['value']), 'Unverified candidate numeric conversion')
            except InvalidOperation:
                raise ImportValidationError('Invalid candidate value')
        if r['pending_status'] == 'pending':
            require(r['import_batch'] in ('quarantine_pending', 'missing_marker', 'test_case'), 'Pending outside quarantine')
            require(not scoring and r['caveat'] and r['missing_materials'], 'Pending needs caveat/materials and scoring=false')
        if r['import_batch'] == 'quarantine_pending':
            require(r['pending_status'] == 'pending', 'Quarantine without pending')
        if r['import_batch'] == 'missing_marker':
            require(r['status'] == 'missing_marker' and r['raw_value'] in ('/', '—', '未披露', '未统计')
                    and not r['value'] and not scoring, 'Invalid missing marker')
    for r in checks:
        require(r['company'] in ('BYD', 'GEELY'), 'Company not enabled for V4 checks')
        require(r['标准结论'] in ('met', 'partial', 'not_met', 'unknown'), 'Invalid check status')
        require(r['rule_version'] == 'disclosure-v0.1', 'Wrong check rule version')
        if r['check_id'].startswith('T'):
            require(r['标准结论'] == 'unknown' and r['pending_status'] == 'pending'
                    and not r['target_key'] and r['target_selection_status'] == 'needs_review', 'Unselected target must remain unknown')


def adapt(package):
    package = Path(package)
    with zipfile.ZipFile(package) as z:
        names = z.namelist()
        require(len(names) == len(set(names)), 'Duplicate archive paths')
        require(all(not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts for n in names), 'Unsafe archive path')
        require(sum(i.file_size for i in z.infolist()) < 600_000_000, 'Package too large')
        maps = [n for n in names if n.endswith('/导入映射.xlsx')]
        require(len(maps) == 1, 'Expected one 导入映射.xlsx')
        root = maps[0].removesuffix('导入映射.xlsx')
        books = {n: workbook(z.read(n)) for n in names if n.endswith('.xlsx')}
        mapping = books[maps[0]]
        reports = table(mapping, '报告版本', 'report_id')
        observations = table(mapping, '证据映射', 'obs_id')
        checks = table(mapping, '检查映射', 'check_id')
        candidates = table(mapping, '目标候选', 'candidate_target_key')
        routing = table(mapping, '分流', 'obs_id')
        rulebook = json.loads(z.read(root + '规则/disclosure-v0.1.json'))
        official = json.loads(Path(__file__).with_name('rules').joinpath('disclosure-v0.1.json').read_text())
        require(rulebook == official, 'Package rulebook differs from product rules')
        report_index = {r['report_id']: r for r in reports}
        require(len(report_index) == len(reports), 'Duplicate report IDs')
        for r in observations:
            for rid in filter(None, re.split(r'[;,，；\s]+', r['supporting_report_ids'])):
                require(rid in report_index, 'Unknown supporting report ID')
        pdf_hashes = {hashlib.sha256(z.read(n)).hexdigest(): n for n in names if n.endswith('.pdf')}
        for r in reports:
            require(re.fullmatch('[0-9a-f]{64}', r['SHA-256']), 'Invalid report hash')
            if r['是否入包'] == '是':
                require(r['SHA-256'] in pdf_hashes, f"Missing/mismatched PDF: {r['report_id']}")
        evidence = {}
        def register(eid, rid, page, text, source, kind):
            require(eid and rid in report_index and text.strip(), f'Incomplete evidence: {eid}')
            pages = [int(x) for x in re.findall(r'\d+', page)]
            require((pages or kind == 'derived_reference') and all(1 <= p <= int(report_index[rid]['页数']) for p in pages), f'Invalid physical page: {eid}')
            item = dict(evidence_id=eid, report_id=rid, physical_pages=pages, text=text,
                        source=source, kind=kind, verification_status='unreviewed')
            require(eid not in evidence or evidence[eid] == item, f'Conflicting evidence ID: {eid}')
            evidence[eid] = item
        for r in observations:
            matches = [n for n in books if PurePosixPath(n).name == r['source_workbook']]
            require(len(matches) == 1, 'Ambiguous/missing source workbook')
            rows = books[matches[0]][r['source_sheet']]
            physical = next((cells for row, cells in rows if row == int(r['row_ref'])), None)
            require(physical is not None, 'Missing physical source row')
            # Locate the source header, which differs between the BYD and Geely books.
            hdr = next(c for _, c in rows if '原文摘录' in c.values())
            src = {v: physical.get(k, '') for k, v in hdr.items() if v}
            require(src.get('evidence_id') == r['evidence_id'] and src.get('report_id', r['report_id']) == r['report_id'], 'Source row ID mismatch')
            if src.get('年度'):
                require(src['年度'] == r['year'], 'Source year mismatch')
            if r['import_batch'] == 'final_candidate':
                source_value = src.get('raw_value', src.get('原始数值'))
                if source_value is not None:
                    require(source_value.replace(',', '') == r['raw_value'].replace(',', ''), 'Source value mismatch')
            register(r['evidence_id'], r['report_id'], r['physical_page'], src['原文摘录'],
                     f"{r['source_workbook']}/{r['source_sheet']}!{r['row_ref']}",
                     'derived_reference' if r['status'] == 'derived' and r['import_batch'] == 'test_case' and not boolean(r['usable_for_scoring']) else 'observation')
        for r in checks:
            matches = [n for n in books if PurePosixPath(n).name == r['source_workbook']]
            require(len(matches) == 1, 'Missing check workbook')
            source = table(books[matches[0]], r['source_sheet'], 'check_id')
            src = next((x for x in source if x['check_id'] == r['check_id']), None)
            require(src and src['evidence_id'] == r['evidence_id'] and src['report_id'] == r['report_id']
                    and src['标准结论'] == r['标准结论'], 'Check source mismatch')
            register(r['evidence_id'], r['report_id'], r['物理页'], src['原文摘录'],
                     f"{r['source_workbook']}/{r['source_sheet']}!{src['_row']}", 'check')
        for r in candidates:
            require(r['target_selection_status'] == 'needs_review', 'Unapproved candidate selection')
            register(r['evidence_id'], r['report_id'], r['证据位置'], r['原文摘录'], '导入映射.xlsx/目标候选', 'target_candidate')
        validate_entries(observations, checks)
        require(all(r['obs_id'] for r in observations), 'Empty observation ID')
        require(len({r['obs_id'] for r in observations}) == len(observations), 'Duplicate observation IDs')
        obs = {r['obs_id']: r for r in observations}
        require(len({r['obs_id'] for r in routing}) == len(routing), 'Duplicate route')
        for r in routing:
            require(r['obs_id'] in obs, 'Unknown route observation')
            source = obs[r['obs_id']]
            require(all(r[k] == source[k] for k in ('report_id', 'evidence_id')), 'Route reference mismatch')
            require(r['batch'] == source['import_batch'], 'Conflicting route batch')
            for field in ('year', 'related_pend', 'caveat', 'missing_materials'):
                if field in r:
                    require(r[field] == source.get(field, ''), f'Conflicting route {field}')
        require({r['obs_id'] for r in routing} == {r['obs_id'] for r in observations if r['import_batch'] in ('final_candidate','quarantine_pending','missing_marker')}, 'Incomplete routing')
        formal = {c['check_id'] for c in official['checks']}
        for company in {r['company'] for r in checks}:
            group = [r for r in checks if r['company'] == company]
            require(len(group) == 20 and {r['check_id'] for r in group} == formal, 'Expected 20 distinct official checks per company')
        return dict(package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(), rule_version=official['rule_version'],
                    reports=reports, evidence=list(evidence.values()), observations=observations, checks=checks, target_candidates=candidates,
                    warnings=[dict(source=n, details=b['__warnings__']) for n,b in books.items() if b['__warnings__']])


def dry_run(package, output):
    """Fail before writing on invalid input; never open the production task repository."""
    data = adapt(package)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    counts = dict(Counter(r['import_batch'] for r in data['observations']))
    db_path = output / 'staging.sqlite3'
    with sqlite3.connect(db_path) as db:
        for kind in ('reports', 'evidence', 'observations', 'checks', 'target_candidates'):
            db.execute(f'CREATE TABLE {kind} (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            for item in data[kind]:
                row = dict(item, review_state='unreviewed', confirmed=False)
                if kind == 'checks':
                    row['data_role'] = 'test_case' if row['company'] == 'GEELY' else 'business_candidate'
                if kind == 'observations':
                    row['usable'] = boolean(row['usable'])
                    row['usable_for_scoring'] = boolean(row['usable_for_scoring'])
                db.execute(f'INSERT INTO {kind}(payload) VALUES (?)', (json.dumps(row, ensure_ascii=False),))
        for name, batch in [('final_candidates','final_candidate'), ('quarantine_pending','quarantine_pending'), ('test_cases','test_case')]:
            db.execute(f"CREATE VIEW {name} AS SELECT payload FROM observations WHERE json_extract(payload, '$.import_batch') = '{batch}'")
    summary = dict(status='staging_dry_run_passed', package_sha256=data['package_sha256'],
                   counts={k: len(data[k]) for k in ('reports','evidence','observations','checks','target_candidates')},
                   routing=counts, warnings=data['warnings'], derived_references_without_pdf_page=sum(not e['physical_pages'] for e in data['evidence']), final_score=None, production_written=False, automatically_confirmed=0,
                   pending_samples=[r for r in data['observations'] if r['import_batch'] == 'quarantine_pending'],
                   limitations=['Staging only; no production MetricRecord conversion or scoring approval',
                                'Source excerpts registered but not semantically verified against PDF',
                                'Test-case answers isolated by import_batch; not model inputs'])
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary
