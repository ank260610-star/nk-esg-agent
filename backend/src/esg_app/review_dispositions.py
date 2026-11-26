"""Human review completion is independent of factual certainty and scoring."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .parse_repository import read_business, save_business, bound_reports
from .parse_service import ParseError
from .review import invalidate_checks_for_metric
from .rules_registry import METRIC_TO_CHECKS


def can_review(metadata, actor):
    owners = [scope.get(key) for scope in [metadata, metadata.get('task', {})]
              for key in ['owner', 'user_id'] if scope.get(key)]
    return not owners or all(owner == actor for owner in owners)


def target_fingerprint(target):
    # Unrelated task writes must not reopen a review; changes to the record do.
    payload = {k: v for k, v in target.items() if k != 'revision'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def targets(state):
    return {(kind, item[id_key]): item
            for kind, collection, id_key in [('metric', 'records', 'record_id'), ('check', 'checks', 'check_id')]
            for item in state.get(collection, []) if item.get('is_current') and item.get(id_key)}


def current_dispositions(state):
    current = targets(state)
    latest = {(d['target_type'], d['target_id']): d for d in state.get('review_dispositions', [])}
    return [d for key, d in latest.items()
            if key in current and d['decision'] != 'reopen'
            and d['fingerprint'] == target_fingerprint(current[key])]


def prior_disposition(state, item, kind):
    latest = {(d['target_type'], d['target_id']): d for d in state.get('review_dispositions', [])}
    key = item.get('record_id' if kind == 'metric' else 'check_id')
    own = latest.get((kind, key))
    if own:
        return own
    # A manual successor must not silently erase an unresolved limitation.
    records = {r['record_id']: r for r in state.get('records', [])}
    seen = set()
    while kind == 'metric' and key in records and key not in seen:
        seen.add(key)
        key = records[key].get('supersedes_id')
        ancestor = latest.get((kind, key))
        if ancestor and ancestor['decision'] in ('insufficient_evidence', 'reopen'):
            return ancestor
    return None


def pending_uncertainties(state):
    pending = []
    for (kind, identifier), item in targets(state).items():
        d = prior_disposition(state, item, kind)
        if d and d['decision'] in ('insufficient_evidence', 'reopen'):
            pending.append(dict(d, target_type=kind, target_id=identifier,
                                source_target_id=d['target_id'],
                                needs_recheck=d['decision'] == 'reopen' or d['target_id'] != identifier
                                or d['fingerprint'] != target_fingerprint(item)))
    return pending


def visible_dispositions(state):
    """Export unresolved limitations even when their old approval is stale."""
    visible = {(d['target_type'], d['target_id']): d for d in current_dispositions(state)}
    visible.update({(d['target_type'], d['target_id']): d for d in pending_uncertainties(state)})
    return list(visible.values())


def review_complete(state, item, kind):
    prior = prior_disposition(state, item, kind)
    if prior:
        return prior['target_id'] == item.get('record_id' if kind == 'metric' else 'check_id') and (
            prior['decision'] != 'reopen' and prior['fingerprint'] == target_fingerprint(item))
    return item.get("review_state") == "confirmed" and item.get("status") in (
        ("extracted", "missing_marker") if kind == "metric" else ("met", "partial", "not_met", "not_applicable"))


def metric_restricted_check_ids(state):
    bound = targets(state)
    blocked = set()
    for d in pending_uncertainties(state):
        if d['target_type'] == 'metric':
            metric = bound[('metric', d['target_id'])]['metric_id']
            blocked.update(METRIC_TO_CHECKS.get(metric, ()))
    return blocked


def restricted_check_ids(state):
    return metric_restricted_check_ids(state) | {
        d['target_id'] for d in pending_uncertainties(state) if d['target_type'] == 'check'}


def effective_checks(state, checks):
    blocked = restricted_check_ids(state)
    return [dict(c, status='unknown', review_state='unreviewed') if c['check_id'] in blocked else c
            for c in checks]


def save_disposition(app, task_id, actor, body, evidence):
    if not isinstance(actor, str) or not actor.strip() or actor.startswith('agent:'):
        raise ParseError('FORBIDDEN', 403, 'Human user required')
    allowed = {'expected_revision', 'target_type', 'target_id', 'decision', 'reason',
               'search_scope', 'missing_material', 'limitations', 'resolution_evidence_ids'}
    if not isinstance(body, dict) or set(body) - allowed:
        raise ParseError('INVALID_INPUT', 422, 'Invalid review disposition')
    revision = body.get('expected_revision')
    if type(revision) is not int or revision < 0:
        raise ParseError('INVALID_INPUT', 422, 'expected_revision must be an integer')
    if body.get('target_type') not in ('metric', 'check') or body.get('decision') not in ('confirm', 'insufficient_evidence', 'reopen', 'resolve'):
        raise ParseError('INVALID_INPUT', 422, 'Invalid review target or decision')
    if not isinstance(body.get('target_id'), str) or not 1 <= len(body['target_id']) <= 128:
        raise ParseError('INVALID_INPUT', 422, 'Invalid target ID')
    fields = ['reason', 'search_scope', 'missing_material', 'limitations']
    required = fields if body['decision'] == 'insufficient_evidence' else ['reason']
    for field in fields:
        if field in body and (not isinstance(body[field], str) or len(body[field]) > 4000):
            raise ParseError('INVALID_INPUT', 422, f'{field} must be text (at most 4000 characters)')
        if field in required and not body.get(field, '').strip():
            raise ParseError('INVALID_INPUT', 422, f'{field} is required')
    available = {e.evidence_id: e for e in evidence(task_id)}
    with app.state.tasks.transaction() as db:
        row = db.execute('SELECT revision,results FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        if row is None:
            raise ParseError('NOT_FOUND', 404, 'Task not found')
        metadata = json.loads(row[1])
        if not can_review(metadata, actor):
            raise ParseError('FORBIDDEN', 403, 'Task belongs to another user')
        if revision != row[0]:
            raise ParseError('REVISION_CONFLICT', 409, 'Task changed; refresh before reviewing', task_id, row[0])
        if db.execute('SELECT 1 FROM agent_run_claims WHERE task_id=?', (task_id,)).fetchone():
            raise ParseError('RUN_BUSY', 409, 'Wait for the active run before reviewing', task_id, revision)
        state = read_business(db, task_id)
        target = targets(state).get((body['target_type'], body['target_id']))
        if target is None:
            raise ParseError('NOT_FOUND', 404, 'Current target not found in this task')
        # Bind audit evidence to the actual task; a guessed foreign ID cannot close review.
        ids = target.get('evidence_ids', [])
        if not set(ids) <= set(available):
            raise ParseError('FOREIGN_EVIDENCE', 422, 'Target evidence is unavailable in this task')
        is_metric = body['target_type'] == 'metric'
        valid = ('extracted', 'missing_marker') if is_metric else ('met', 'partial', 'not_met', 'not_applicable')
        if body['decision'] in ('confirm', 'resolve') and (target.get('status') not in valid or not ids
                or (not is_metric and target['check_id'] in metric_restricted_check_ids(state))):
            raise ParseError('INVALID_STATE', 422, 'Unknown facts cannot be confirmed; record a limitation instead')
        prior = prior_disposition(state, target, body['target_type'])
        pending = prior and prior['decision'] in ('insufficient_evidence', 'reopen')
        if body['decision'] == 'confirm' and pending:
            raise ParseError('INVALID_STATE', 422, 'Resolve the outstanding limitation with new evidence, or retain it')
        resolution_ids = body.get('resolution_evidence_ids', [])
        if not isinstance(resolution_ids, list) or len(resolution_ids) > 100 or any(not isinstance(e, str) for e in resolution_ids):
            raise ParseError('INVALID_INPUT', 422, 'Invalid resolution evidence IDs')
        if body['decision'] == 'resolve':
            if not pending or not resolution_ids or not set(resolution_ids) <= set(available):
                raise ParseError('INVALID_INPUT', 422, 'Resolution requires an outstanding case and task-bound new evidence')
            if not set(resolution_ids) - set(prior.get('evidence_ids', [])):
                raise ParseError('INVALID_INPUT', 422, 'Existing evidence alone does not resolve a missing-material case')
        elif resolution_ids:
            raise ParseError('INVALID_INPUT', 422, 'Resolution evidence is only accepted for resolve')
        if is_metric and body['decision'] in ('confirm', 'resolve'):
            from .metric_review import _evidence_supports
            if not any(_evidence_supports(available[eid], target) for eid in ids):
                raise ParseError('INVALID_STATE', 422, 'Evidence does not support this record and year')
        if is_metric and body['decision'] == 'insufficient_evidence' and (
            target.get('status') not in ('extracted', 'missing_marker', 'unknown')
            or '候选原值、单位' in target.get('extraction_note', '')
        ):
            raise ParseError('INVALID_STATE', 422, 'Resolve technical extraction errors before closing public-data review')
        previous = dict(target)
        target['review_state'] = 'confirmed' if body['decision'] in ('confirm', 'resolve') else 'unreviewed'
        if not is_metric and body['decision'] == 'insufficient_evidence':
            target['status'] = 'unknown'
        new_revision = revision + 1
        target['revision'] = new_revision
        if is_metric:
            dependent = {c['check_id']: c for c in state['checks']}
            state['checks'] = list(invalidate_checks_for_metric(dependent, target['metric_id'], new_revision).values())
        for comparison in state['comparisons']:
            comparison['is_current'] = False
        state.pop('score', None)
        record = {k: body[k].strip() for k in ['reason', 'search_scope', 'missing_material', 'limitations'] if isinstance(body.get(k), str)}
        metadata = json.loads(row[1])
        record['report_versions'] = [{k: r.get(k) for k in ['report_id', 'sha256', 'report_year', 'parser_version']}
                                     for r in metadata.get('reports', []) if r.get('report_id') in bound_reports(metadata)]
        record['resolution_evidence_ids'] = resolution_ids
        record['evidence_refs'] = [{k: available[eid].to_dict().get(k)
                                   for k in ['evidence_id', 'report_id', 'pdf_page', 'printed_page']} for eid in dict.fromkeys(ids + resolution_ids)]
        if body['decision'] == 'reopen' and prior:
            for key in ['search_scope', 'missing_material', 'limitations']:
                record[key] = record.get(key) or prior.get(key, '')
        record.update(target_type=body['target_type'], target_id=body['target_id'], decision=body['decision'],
                      actor=actor, at=datetime.now(timezone.utc).isoformat(), revision=new_revision,
                      evidence_ids=ids, previous_target=previous, fingerprint=target_fingerprint(target))
        state.setdefault('review_dispositions', []).append(record)
        save_business(db, task_id, state)
        db.execute('UPDATE tasks SET revision=? WHERE task_id=?', (new_revision, task_id))
    return {'ok': True, 'data': record, 'error': None, 'task_id': task_id, 'revision': new_revision}
