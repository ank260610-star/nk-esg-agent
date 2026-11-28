"""Synthetic packages only; never expose real reference answers in repository fixtures."""
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from esg_app.import_v4 import adapt, boolean, dry_run, ImportValidationError, workbook


def xlsx(sheets):
    output = io.BytesIO()
    def col(i):
        result=''
        while i:
            i,r=divmod(i-1,26);result=chr(65+r)+result
        return result
    with zipfile.ZipFile(output,'w') as z:
        names=list(sheets)
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{n}" sheetId="{i}" r:id="r{i}"/>' for i,n in enumerate(names,1))+'</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships>'+''.join(f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(names)+1))+'</Relationships>')
        for i,rows in enumerate(sheets.values(),1):
            xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            for n,row in enumerate(rows,1):
                xml+=f'<row r="{n}">'+''.join(f'<c r="{col(j)}{n}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'for j,v in enumerate(row,1))+'</row>'
            z.writestr(f'xl/worksheets/sheet{i}.xml',xml+'</sheetData></worksheet>')
    return output.getvalue()


def grid(rows):
    keys=list(dict.fromkeys(k for r in rows for k in r))
    return [keys]+[[r.get(k,'')for k in keys]for r in rows]


@pytest.fixture
def package(tmp_path):
    rule=json.loads(Path('backend/src/esg_app/rules/disclosure-v0.1.json').read_text())
    obs=dict(company='BYD',source_workbook='source.xlsx',source_sheet='observations',row_ref='2',obs_id='o1',report_id='r1',supporting_report_ids='',evidence_id='e1',physical_page='1',status='disclosed',year='2024',raw_value='100',value='100',unit_raw='tCO2e',unit_canonical='tCO2e',usable='1',usable_for_scoring='1',pending_status='none',import_batch='final_candidate',caveat='',missing_materials='')
    checks=[dict(company='BYD',check_id=c['check_id'],标准结论='unknown'if c['check_id'].startswith('T')else'met',rule_version='disclosure-v0.1',pending_status='pending'if c['check_id'].startswith('T')else'none',target_key='',target_selection_status='needs_review'if c['check_id'].startswith('T')else'',source_workbook='source.xlsx',source_sheet='checks',report_id='r1',evidence_id='e_'+c['check_id'],物理页='1',原文摘录='synthetic check excerpt')for c in rule['checks']]
    candidate=dict(candidate_target_key='candidate1',report_id='r1',evidence_id='target1',证据位置='P1',原文摘录='synthetic target',target_selection_status='needs_review')
    reports=[dict(report_id='r1',**{'SHA-256':hashlib.sha256(b'synthetic pdf').hexdigest(),'页数':'1','是否入包':'是'})]
    def build(mutate=lambda x:None):
        data={'obs':[obs.copy()],'checks':[c.copy()for c in checks],'candidates':[candidate.copy()],'reports':[r.copy()for r in reports]};mutate(data)
        route=[dict(obs_id=r['obs_id'],report_id=r['report_id'],evidence_id=r['evidence_id'],batch=r['import_batch'])for r in data['obs']]
        mapping={'报告版本':grid(data['reports']),'证据映射':grid(data['obs']),'检查映射':grid(data['checks']),'目标候选':grid(data['candidates']),'分流':grid(route)}
        source={'observations':grid([dict(evidence_id='e1',report_id='r1',原文摘录='2024 100 tCO2e')]),'checks':grid(checks)}
        path=tmp_path/'package.zip'
        with zipfile.ZipFile(path,'w')as z:
            z.writestr('v4/导入映射.xlsx',xlsx(mapping));z.writestr('v4/source.xlsx',xlsx(source))
            z.writestr('v4/规则/disclosure-v0.1.json',json.dumps(rule));z.writestr('v4/report.pdf',b'synthetic pdf')
        return path
    return build


def test_registered_sources_and_isolated_sqlite(package,tmp_path):
    p=package();out=tmp_path/'out';result=dry_run(p,out)
    assert result['counts']['evidence']==22 and result['automatically_confirmed']==0
    imported=adapt(p)['evidence']
    assert next(e for e in imported if e['evidence_id']=='e1')['column_year']==2024
    assert next(e for e in imported if e['evidence_id']=='target1')['column_year'] is None
    assert result['production_written']is False and result['final_score']is None
    with sqlite3.connect(out/'staging.sqlite3')as db:
        candidate=json.loads(db.execute('SELECT payload FROM final_candidates').fetchone()[0])
        assert candidate['confirmed']is False and candidate['review_state']=='unreviewed'
        assert candidate['usable_for_scoring']is True
        assert db.execute('SELECT COUNT(*) FROM quarantine_pending').fetchone()[0]==0
    with pytest.raises(FileExistsError):dry_run(p,out)


@pytest.mark.parametrize('patch',[
    {'usable':'0'}, {'usable_for_scoring':'false'}, {'pending_status':'pending'},
    {'value':'NaN'}, {'year':'wrong'}, {'unit_canonical':''}, {'import_batch':'other'},
    {'supporting_report_ids':'foreign'}, {'evidence_id':'foreign'}, {'physical_page':'2'},
])
def test_invalid_candidate_is_rejected_before_any_output(package,tmp_path,patch):
    p=package(lambda d:d['obs'][0].update(patch));out=tmp_path/'out'
    with pytest.raises(ImportValidationError):dry_run(p,out)
    assert not out.exists()


def test_pending_preserved_and_boolean_false_not_truthy(package,tmp_path):
    def patch(d):d['obs'][0].update(import_batch='quarantine_pending',pending_status='pending',usable_for_scoring='false',caveat='cannot compare',missing_materials='internal factor table')
    result=dry_run(package(patch),tmp_path/'out')
    assert result['routing']=={'quarantine_pending':1}
    assert result['pending_samples'][0]['caveat']=='cannot compare'
    with sqlite3.connect(tmp_path/'out/staging.sqlite3')as db:
        row=json.loads(db.execute('SELECT payload FROM quarantine_pending').fetchone()[0])
        assert row['usable_for_scoring']is False and row['confirmed']is False


def test_unknown_target_cannot_be_promoted(package):
    with pytest.raises(ImportValidationError):adapt(package(lambda d:d['checks'][-1].update(target_key='chosen')))


def test_duplicate_observation_fails(package):
    with pytest.raises(ImportValidationError):adapt(package(lambda d:d['obs'].append(d['obs'][0].copy())))


def test_invalid_boolean():
    assert boolean('false')is False
    with pytest.raises(ImportValidationError):boolean('yes')


def test_blind_test_answers_cannot_enter_business_candidates(package):
    with pytest.raises(ImportValidationError,match='Test answers'):
        adapt(package(lambda d:d['obs'][0].update(company='GEELY')))


def test_missing_marker_keeps_null_value(package,tmp_path):
    def patch(d):d['obs'][0].update(import_batch='missing_marker',status='missing_marker',raw_value='/',value='',usable_for_scoring='0',pending_status='pending',caveat='not zero',missing_materials='inventory')
    dry_run(package(patch),tmp_path/'out')
    with sqlite3.connect(tmp_path/'out/staging.sqlite3')as db:
        row=json.loads(db.execute('SELECT payload FROM observations').fetchone()[0])
        assert row['raw_value']=='/' and row['value']=='' and not row['usable_for_scoring']


def test_unknown_excel_formulas_are_not_executed():
    original=xlsx({'sheet':[['value'],['100']]});output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original))as source,zipfile.ZipFile(output,'w')as target:
        for name in source.namelist():
            data=source.read(name)
            if name=='xl/worksheets/sheet1.xml':data=data.replace(b'<is><t>100</t></is>',b'<f>1+1</f><v>2</v>')
            target.writestr(name,data)
    with pytest.raises(ImportValidationError,match='Formula'):workbook(output.getvalue())


@pytest.mark.parametrize('patch',[{'unit_canonical':'MWh'},{'raw_value':'999'}])
def test_unverified_conversion_rejected(package,patch):
    with pytest.raises(ImportValidationError):adapt(package(lambda d:d['obs'][0].update(patch)))
