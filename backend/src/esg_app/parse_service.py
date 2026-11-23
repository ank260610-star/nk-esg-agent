"""parse_report：授权、幂等重放、版本检查、解析和原子提交。"""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from importlib.metadata import version as package_version
from uuid import UUID

from .parsing import Word, reconstruct_table, extract_footnotes, footnote_references
from .parse_repository import token_identity, bound_reports, read_business, save_business
from .schemas import make_issue

PARSER_VERSION = f"pdfplumber-{package_version('pdfplumber')}+coord-contract-v2"
MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 300


class ParseError(Exception):
    def __init__(self, code: str, status: int, message: str, task_id=None, revision=None):
        super().__init__(message)
        self.status = status
        self.payload = dict(ok=False, data=None, error=dict(code=code, message=message, retryable=False),
                            task_id=task_id, revision=revision)


def parse_pdf(data: bytes) -> dict:
    """缓存报告无关的原页内容，复用交付的通用坐标还原引擎。"""
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    if not data.startswith(b'%PDF'):
        raise ParseError('INVALID_PDF', 422, 'Invalid PDF')
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        # pdfplumber 的版本可能直接抛出或包装 PDFPasswordIncorrect。
        encrypted = any(isinstance(e, PDFPasswordIncorrect)
                        for e in (exc, exc.__cause__, exc.__context__, *exc.args))
        raise ParseError('ENCRYPTED_PDF' if encrypted else 'INVALID_PDF', 422, 'PDF cannot be opened') from None
    with pdf:
        if pdf.doc.encryption:
            raise ParseError('ENCRYPTED_PDF', 422, 'Encrypted PDF is not supported')
        try:
            pdf_pages = pdf.pages
        except Exception:
            raise ParseError('INVALID_PDF', 422, 'PDF pages cannot be read') from None
        if not 1 <= len(pdf_pages) <= MAX_PAGES:
            raise ParseError('PAGE_LIMIT', 413, 'PDF page limit exceeded')
        pages = []
        for number, page in enumerate(pdf_pages, 1):
            try:
                text = page.extract_text() or ''
            except Exception:
                pages.append(dict(number=number, text='', rows=[], issue='PAGE_PARSE_FAILED'))
                continue
            if not text.strip():
                pages.append(dict(number=number, text='', rows=[], issue='NO_TEXT_LAYER'))
                continue
            rows, notes, binding_issues, issue = [], [], [], None
            try:
                words = [Word(**{k: w[k] for k in ('text', 'x0', 'x1', 'top', 'bottom')})
                         for w in page.extract_words()]
                notes = extract_footnotes(words)
                note_words = {w for note in notes for w in note.words}
                grid = reconstruct_table([w for w in words if w not in note_words])
                binding_issues = grid.issues
                rows = [dict(label=r.label, unit=r.unit, values=r.values, bbox=r.bbox,
                             table_index=r.table_index, footnote_refs=footnote_references(r.label))
                        for r in grid.rows if r.values]
            except Exception:
                issue = 'TABLE_PARSE_FAILED'
            # 拆表失败时仍保留已经成功读取的原文，不能静默丢弃整个页面。
            pages.append(dict(number=number, text=text, rows=rows, issue=issue, issues=binding_issues,
                              footnotes=[dict(number=n.number, text=n.text, bbox=n.bbox) for n in notes]))
        return dict(page_count=len(pages), pages=pages)


def materialize(report_id: str, digest: str, version: str, parsed: dict) -> tuple[dict, list[dict]]:
    evidence, issues = [], []
    for page in parsed['pages']:
        number = page['number']
        page_codes = set()
        def warn(code):
            if code not in page_codes:
                issues.append(make_issue(code, 'Page contains unresolved parsing or cell bindings', report_id, number))
                page_codes.add(code)
        for code in ([page['issue']] if page['issue'] else []) + page.get('issues', []):
            warn(code)
        if not page['text']:
            continue

        def add(kind, text, label=None, year=None, bbox=None, table_id=None, linked=None):
            created = []
            # 遵循 Evidence.text 的 20000 字上限；印刷页未知时保持 null。
            for start in range(0, len(text), 20000):
                identity = f'{report_id}:{digest}:{version}:{number}:{len(evidence)}'
                evidence.append(dict(
                    evidence_id='ev_' + hashlib.sha256(identity.encode()).hexdigest()[:32],
                    report_id=report_id, pdf_page=number, printed_page=None, kind=kind,
                    text=text[start:start + 20000], bbox=bbox, table_id=table_id,
                    row_label=label[:20000] if label is not None else None,
                    column_year=year, linked_evidence_ids=list(linked or []),
                    viewer_url=f'/api/reports/{report_id}/pdf#page={number}'))
                created.append(evidence[-1])
            return created

        add('text', page['text'])
        notes_by_number = {}
        for note in page.get('footnotes', []):
            created = add('footnote', note['text'], bbox=note.get('bbox'))
            if note['number'] in notes_by_number:
                # 重号定义保留原文，但不能任意选一个关联。
                notes_by_number[note['number']] = []
                warn('AMBIGUOUS_FOOTNOTE_REFERENCE')
            else:
                notes_by_number[note['number']] = created
        for row in page['rows']:
            linked_notes = [e for ref in row.get('footnote_refs', []) for e in notes_by_number.get(ref, [])]
            for year, value in row['values'].items():
                table_id = f"table_{digest[:12]}_{number}_{row.get('table_index', 0)}"
                cells = add('table', f"{row['label']} {year} {value} {row['unit'] or ''}",
                            row['label'], int(year), row.get('bbox'), table_id,
                            [e['evidence_id'] for e in linked_notes][:100])
                for note in linked_notes:
                    links = list(dict.fromkeys(note['linked_evidence_ids'] + [e['evidence_id'] for e in cells]))
                    if len(links) > 100:
                        warn('EVIDENCE_LINK_LIMIT')
                    note['linked_evidence_ids'] = links[:100]
    summary = dict(report_id=report_id, status=('failed' if not evidence else 'partial' if issues else 'parsed'),
        page_count=parsed['page_count'], parsed_pages=sum(bool(p['text']) for p in parsed['pages']),
        evidence_count=len(evidence), issues=issues[:100])
    return summary, evidence


class ParseService:
    def __init__(self, repository, paths: dict, parser=parse_pdf, version: str = PARSER_VERSION):
        self.repo, self.paths, self.parser, self.version = repository, paths, parser, version

    def run(self, task_id: str, token: str | None, key: str | None, body) -> dict:
        bound = self.repo.task_for_agent_token(token)
        if bound is None:
            raise ParseError('UNAUTHORIZED', 401, 'Missing or invalid task token')
        if bound != task_id:
            raise ParseError('FORBIDDEN', 403, 'Task access denied')
        task = self.repo.get(task_id)
        if task is None:
            raise ParseError('NOT_FOUND', 404, 'Task not found')

        def fail(code, status, message):
            raise ParseError(code, status, message, task_id, task.revision)

        if (not isinstance(body, dict) or set(body) != {'expected_revision'}
                or type(body['expected_revision']) is not int or body['expected_revision'] < 0):
            fail('INVALID_INPUT', 422, 'Only nonnegative integer expected_revision is accepted')
        try:
            UUID(key)
        except (ValueError, TypeError, AttributeError):
            fail('INVALID_INPUT', 422, 'UUID Idempotency-Key is required')
        identity = token_identity(token)
        request_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        params = (identity, task_id, 'parse_report', key)

        def replay(db):
            # 解析期间令牌被撤销或重新绑定时，不提交也不泄露先前响应。
            authorized = db.execute('SELECT task_id FROM tokens WHERE identity=?', (identity,)).fetchone()
            if not authorized or authorized[0] != task_id:
                raise ParseError('FORBIDDEN', 403, 'Task access changed')
            row = db.execute('SELECT request_hash,response FROM idempotency '
                'WHERE identity=? AND task_id=? AND tool=? AND key=?', params).fetchone()
            if row:
                if row[0] != request_hash:
                    revision = db.execute('SELECT revision FROM tasks WHERE task_id=?', (task_id,)).fetchone()[0]
                    raise ParseError('IDEMPOTENCY_CONFLICT', 409, 'Key was used for another request', task_id, revision)
                return json.loads(row[1])

        with self.repo.transaction() as db:
            old = replay(db)
            if old:
                return old
            revision, results = db.execute('SELECT revision,results FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            if revision != body['expected_revision']:
                raise ParseError('REVISION_CONFLICT', 409, 'Read the latest task before deciding again', task_id, revision)
            report_ids = bound_reports(json.loads(results))
        if (not isinstance(report_ids, list) or not 1 <= len(report_ids) <= 2
                or any(not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', rid)
                       for rid in report_ids) or len(set(report_ids)) != len(report_ids)):
            fail('INVALID_STATE', 422, 'Task must bind one or two valid report IDs')

        prepared = []
        for rid in report_ids:
            path = self.paths.get(rid) or self.repo.report_path(rid)
            if path is None:
                fail('NOT_FOUND', 404, 'Bound report is not registered')
            try:
                with open(path, 'rb') as file:
                    data = file.read(MAX_BYTES + 1)
            except OSError:
                fail('NOT_FOUND', 404, 'Bound report is unavailable')
            if len(data) > MAX_BYTES:
                fail('FILE_TOO_LARGE', 413, 'PDF exceeds 50 MiB')
            digest = hashlib.sha256(data).hexdigest()
            with self.repo.transaction() as db:
                cache = db.execute('SELECT data FROM parse_cache WHERE hash=? AND version=?', (digest, self.version)).fetchone()
            try:
                parsed = json.loads(cache[0]) if cache else self.parser(data)
            except ParseError as exc:
                exc.payload.update(task_id=task_id, revision=task.revision)
                raise
            summary, evidence = materialize(rid, digest, self.version, parsed)
            prepared.append((rid, digest, str(Path(path).resolve()), parsed, summary, evidence))

        if not any(p[5] for p in prepared):
            no_text = all(page['issue'] == 'NO_TEXT_LAYER' for p in prepared for page in p[3]['pages'])
            fail('NO_TEXT_LAYER' if no_text else 'PARSE_FAILED', 422 if no_text else 500,
                 'No extractable text; OCR is not enabled' if no_text else 'No pages could be parsed')

        with self.repo.transaction() as db:
            old = replay(db)
            if old:
                return old
            revision, latest = db.execute('SELECT revision,results FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            if revision != body['expected_revision'] or bound_reports(json.loads(latest)) != report_ids:
                raise ParseError('REVISION_CONFLICT', 409, 'Task changed while parsing', task_id, revision)
            changed = False
            for rid, digest, path, parsed, summary, evidence in prepared:
                previous = db.execute('SELECT hash,version FROM task_parses WHERE task_id=? AND report_id=?', (task_id, rid)).fetchone()
                changed |= previous != (digest, self.version)
                db.execute('INSERT OR REPLACE INTO parse_cache VALUES(?,?,?)', (digest, self.version, json.dumps(parsed)))
                db.execute('INSERT OR REPLACE INTO task_parses VALUES(?,?,?,?,?,?)',
                    (task_id, rid, digest, self.version, json.dumps(summary), json.dumps(evidence)))
                db.execute('INSERT OR REPLACE INTO reports VALUES(?,?)', (rid, path))
            summaries = [p[4] for p in prepared]
            summary = dict(status='partial' if any(s['status'] != 'parsed' for s in summaries) else 'parsed',
                           reports=summaries, issues=[i for s in summaries for i in s['issues']][:100])
            if changed and db.execute('SELECT 1 FROM business_state WHERE task_id=?', (task_id,)).fetchone():
                state = read_business(db, task_id)
                for kind in ('records', 'checks', 'comparisons'):
                    for item in state[kind]:
                        item['is_current'] = False
                state.pop('score', None)
                state.pop('extraction_cache', None)
                save_business(db, task_id, state)
            revision += int(changed)
            db.execute('UPDATE tasks SET revision=? WHERE task_id=?', (revision, task_id))
            response = dict(ok=True, data=summary, error=None, task_id=task_id, revision=revision)
            db.execute('INSERT INTO idempotency VALUES(?,?,?,?,?,?)', (*params, request_hash, json.dumps(response)))
            return response
