"""指标人工确认/修正：归属、版本、审计、失效、幂等与事务。

v0.2 契约见 docs/接口契约_指标复核_v0.2.md。所有写入都在
ParseRepository.transaction()（BEGIN IMMEDIATE）内完成，失败整体回滚；
幂等成功结果先于 revision 检查重放，避免超时重放被误判为版本冲突。
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from .metrics import _unit_ok
from .model_extractor import unit_identity, UNIT_ALIASES, normalized
from .parse_repository import bound_reports, read_business, save_business
from .parse_service import ParseError
from .review import METRIC_TO_CHECKS
from .write_service import envelope

TOOL = "metric_reviews"
TOP_FIELDS = frozenset(
    {"action", "record_id", "expected_revision", "reason", "evidence_ids", "correction"}
)
CORRECTION_FIELDS = frozenset(
    {
        "value", "raw_value", "raw_unit", "canonical_unit", "conversion_rule",
        "denominator", "boundary_text", "boundary_key", "method_text",
        "scope3_categories", "extraction_note", "status",
    }
)
STATUSES = frozenset(
    {"extracted", "missing_marker", "unknown", "conflict", "parse_error"}
)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
# 完整数值 token：左右边界禁止紧邻数字或小数点（.1 不得拆出 1、0.1300 不得拆出
# 1300）；整数部分允许严格千分位（1,000.00）；科学计数法整体匹配（1.23e4 不得
# 拆成 1.23 或 4）。匹配结果在原文上取，禁止把换行／空格拼接成新数字。
NUMBER_RE = re.compile(
    r"(?<![\d.,])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\d.,])"
)
MARKERS = ("/", "—", "未披露", "未统计")


def _fail(code: str, status: int, message: str, task_id=None, revision=None) -> None:
    raise ParseError(code, status, message, task_id, revision)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(body: dict) -> str:
    from hashlib import sha256
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _number_supported(value: str, text: str) -> bool:
    try:
        target = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return False
    if not target.is_finite():
        return False
    for token in NUMBER_RE.findall(text):
        try:
            if Decimal(token.replace(",", "")) == target:
                return True
        except InvalidOperation:
            continue
    return False


def _evidence_supports(evidence, record: dict) -> bool:
    """证据须属于记录报告与年度，且实际支持数值或原文标记。"""
    if evidence.report_id != record.get("report_id"):
        return False
    if evidence.column_year is not None and evidence.column_year != record.get("metric_year"):
        return False
    raw_text = evidence.text or ""
    if record.get("status") == "missing_marker":
        marker = str(record.get("raw_value") or "").strip()
        return marker in MARKERS and bool(re.search(r"(?<!\S)" + re.escape(marker) + r"(?!\S)", raw_text))
    if record.get("status") == "extracted":
        raw, value = record.get('raw_value'), record.get('value')
        raw_unit, canonical = record.get('raw_unit'), record.get('canonical_unit')
        if not all(isinstance(x, str) and x.strip() for x in [raw, value, raw_unit, canonical]):
            return False
        try:
            if not Decimal(value).is_finite() or Decimal(raw.replace(',', '')) != Decimal(value):
                return False
        except InvalidOperation:
            return False
        if not _unit_ok(record.get('metric_id'), raw_unit) or not _unit_ok(record.get('metric_id'), canonical):
            return False
        if unit_identity(raw_unit) != unit_identity(canonical):
            return False  # A free-form conversion explanation is not verified arithmetic.
        compact = "".join(normalized(raw_text).split())
        unit = "".join(normalized(raw_unit).split())
        numerator, sep, denominator = unit.partition('/')
        variants = {numerator}
        for aliases in UNIT_ALIASES:
            if numerator in aliases:
                variants |= aliases
        unit_present = any(re.search(r"(?<![a-z万千亿])" + re.escape(v + sep + denominator) + r"(?![a-z/])", compact)
                           for v in variants)
        return unit_present and _number_supported(value, raw_text)
    # unknown/conflict/parse_error 确认的是状态及其说明，不推定数值已提取。
    return True


def _validate_request(body, key) -> tuple:
    if not isinstance(body, dict):
        _fail("INVALID_INPUT", 400, "invalid JSON")
    unknown = sorted(set(body) - TOP_FIELDS)
    if unknown:
        _fail("INVALID_INPUT", 400, "unknown field: " + ", ".join(unknown))
    action = body.get("action")
    if action not in ("confirm", "correct"):
        _fail("INVALID_INPUT", 400, "invalid action")
    for required in ("record_id", "expected_revision", "reason", "evidence_ids"):
        if required not in body:
            _fail("INVALID_INPUT", 400, f"{required} required")
    record_id = body["record_id"]
    if not isinstance(record_id, str) or not ID_RE.fullmatch(record_id):
        _fail("INVALID_INPUT", 400, "invalid record_id")
    expected = body["expected_revision"]
    if type(expected) is not int or expected < 0:
        _fail("INVALID_INPUT", 400, "expected_revision must be a nonnegative integer")
    reason = body["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        _fail("INVALID_INPUT", 400, "reason required")
    evidence_ids = body["evidence_ids"]
    if not isinstance(evidence_ids, list) or not evidence_ids:
        _fail("INVALID_INPUT", 400, "evidence_ids required")
    if (
        any(not isinstance(eid, str) or not ID_RE.fullmatch(eid) for eid in evidence_ids)
        or len(set(evidence_ids)) != len(evidence_ids)
    ):
        _fail("INVALID_INPUT", 400, "invalid evidence_ids")
    correction = body.get("correction")
    if action == "confirm" and correction is not None:
        _fail("INVALID_INPUT", 400, "correction is only allowed for correct")
    if action == "correct" and not isinstance(correction, dict):
        _fail("INVALID_INPUT", 400, "correction required")
    if correction is not None:
        unknown_correction = sorted(set(correction) - CORRECTION_FIELDS)
        if unknown_correction:
            _fail("INVALID_INPUT", 400, "unknown correction field: " + ", ".join(unknown_correction))
        if "status" in correction and correction["status"] not in STATUSES:
            _fail("INVALID_INPUT", 400, "invalid correction status")
    try:
        UUID(str(key))
    except (ValueError, TypeError, AttributeError):
        _fail("INVALID_INPUT", 400, "Idempotency-Key must be a UUID")
    return action, record_id, expected, reason, evidence_ids, correction


def _merge_correction(record: dict, correction: dict, task_id: str, revision: int) -> dict:
    merged = deepcopy(record)
    merged.update(deepcopy(correction))
    status = merged.get("status")
    if status == "extracted":
        raw_unit = merged.get("raw_unit")
        canonical_unit = merged.get("canonical_unit")
        if not isinstance(raw_unit, str) or not _unit_ok(merged.get("metric_id"), raw_unit):
            _fail("INVALID_INPUT", 400, "invalid correction: unit incompatible with metric")
        if not isinstance(canonical_unit, str) or not canonical_unit.strip():
            _fail("INVALID_INPUT", 400, "invalid correction: canonical_unit required")
        if (
            not merged.get("conversion_rule")
            and unit_identity(raw_unit) != unit_identity(canonical_unit)
        ):
            _fail("INVALID_INPUT", 400, "invalid correction: unit mismatch without conversion rule")
        if str(merged.get("raw_value") or "").strip() in MARKERS:
            _fail("INVALID_INPUT", 400, "invalid correction: extracted cannot use a missing marker")
    if status == "missing_marker":
        marker = str(merged.get("raw_value") or "").strip()
        if marker not in MARKERS:
            _fail("INVALID_INPUT", 400, "invalid correction: raw_value marker required")
    # 改变原值/单位/边界时，旧的换算说明与分母不得残留。
    changed = any(
        record.get(field) != merged.get(field)
        for field in ("value", "raw_unit", "canonical_unit", "boundary_text", "boundary_key", "denominator")
    )
    if changed and (record.get("conversion_rule") or record.get("denominator") is not None):
        if "conversion_rule" not in correction or "denominator" not in correction:
            _fail(
                "INVALID_INPUT", 400,
                "invalid correction: conversion_rule and denominator must be restated when value/unit/boundary changes",
            )
    merged.update(
        record_id="mr_" + uuid4().hex[:24],
        task_id=task_id,
        origin="manual",
        supersedes_id=record["record_id"],
        derivation=None,
        review_state="confirmed",
        revision=revision,
        is_current=True,
    )
    return merged


def _invalidate(state: dict, record: dict, new_record: dict | None, new_revision: int) -> dict:
    ids = {record["record_id"]}
    if new_record is not None:
        ids.add(new_record["record_id"])
    comparisons = []
    for item in state.get("comparisons", []):
        if item.get("is_current") and (
            item.get("older_record_id") in ids or item.get("newer_record_id") in ids
        ):
            item["is_current"] = False
            comparisons.append(item.get("comparison_id"))
    checks = []
    affected = METRIC_TO_CHECKS.get(record.get("metric_id"), frozenset())
    for item in state.get("checks", []):
        if item.get("is_current") and item.get("check_id") in affected:
            item.update(
                status="unknown", review_state="unreviewed", is_current=False,
                revision=new_revision,
                reason=f"INVALIDATED_BY_METRIC_REVIEW:{record.get('metric_id')}",
            )
            checks.append(item.get("check_id"))
    score = "absent"
    if state.get("score"):
        state.pop("score", None)
        score = "stale"
    return {"comparisons": comparisons, "checks": checks, "score": score}


def apply_metric_review(app, task_id: str, actor: str, body: dict, key: str | None,
                        export_artifact_ids: list[str] | None = None) -> dict:
    """在单个数据库事务内执行指标确认/修正，返回统一信封。"""
    if not isinstance(actor, str) or not actor.strip() or actor.startswith("agent:"):
        _fail("FORBIDDEN", 403, "agent token cannot review")
    action, record_id, expected, reason, evidence_ids, correction = _validate_request(body, key)
    identity = "user:" + actor
    digest = _digest(body)
    # 证据加载在写锁之外完成；只取当前任务绑定报告范围内的证据。
    evidence = {
        e.evidence_id: e for e in app.state.write_service.evidence(task_id)
    }

    with app.state.tasks.transaction() as db:
        row = db.execute(
            "SELECT revision,results FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            _fail("NOT_FOUND", 404, "task not found", task_id)
        revision, results = row[0], json.loads(row[1])
        from .review_dispositions import can_review
        if not can_review(results, actor):
            _fail("FORBIDDEN", 403, "task owner mismatch")

        # 幂等必须先于版本检查：断连后原请求重放不能被误判为 409。
        replay = db.execute(
            "SELECT request_hash,response FROM idempotency WHERE identity=? AND task_id=? AND tool=? AND key=?",
            (identity, task_id, TOOL, str(key)),
        ).fetchone()
        if replay:
            if replay[0] != digest:
                _fail(
                    "IDEMPOTENCY_CONFLICT", 409,
                    "Key was used for a different request", task_id, revision,
                )
            return json.loads(replay[1])
        if db.execute('SELECT 1 FROM agent_run_claims WHERE task_id=?', (task_id,)).fetchone():
            _fail('RUN_BUSY', 409, 'Wait for the active run before reviewing', task_id, revision)
        if expected != revision:
            _fail(
                "REVISION_CONFLICT", 409,
                f"task changed; expected {expected}, current {revision}", task_id, revision,
            )

        allowed_reports = set(bound_reports(results))
        selected = [
            e for eid, e in evidence.items()
            if eid in evidence_ids and e.report_id in allowed_reports
        ]
        if len(selected) != len(evidence_ids):
            _fail(
                "FOREIGN_EVIDENCE", 400,
                "evidence outside record scope", task_id, revision,
            )

        state = read_business(db, task_id)
        record = next(
            (r for r in state.get("records", []) if r.get("record_id") == record_id),
            None,
        )
        if record is None or record.get("task_id") != task_id:
            _fail("NOT_FOUND", 404, "record not found", task_id, revision)
        if not record.get("is_current"):
            _fail("INVALID_STATE", 409, "record superseded", task_id, revision)
        if any(e.report_id != record.get("report_id") for e in selected):
            _fail(
                "FOREIGN_EVIDENCE", 400,
                "evidence outside record report", task_id, revision,
            )
        if any(
            e.column_year is not None and e.column_year != record.get("metric_year")
            for e in selected
        ):
            _fail(
                "FOREIGN_EVIDENCE", 400,
                "evidence year mismatch", task_id, revision,
            )
        before = deepcopy(record)

        new_revision = revision + 1
        new_record = None
        if action == "correct":
            merged = _merge_correction(record, correction, task_id, new_revision)
            merged["evidence_ids"] = list(evidence_ids)
            from .write_service import validate as validate_record
            if not validate_record("MetricRecord", merged):
                _fail("INVALID_INPUT", 400, "invalid correction: record contract", task_id, revision)
            if not any(_evidence_supports(e, merged) for e in selected):
                _fail("INVALID_INPUT", 400, "evidence does not support corrected value", task_id, revision)
            new_record = merged
            record["is_current"] = False
            state["records"].append(new_record)
            current = new_record
        else:
            if not any(_evidence_supports(e, record) for e in selected):
                _fail("INVALID_INPUT", 400, "evidence does not support confirmed record", task_id, revision)
            record["review_state"] = "confirmed"
            record["revision"] = new_revision
            current = record

        invalidated = _invalidate(state, record, new_record, new_revision)
        invalidated["exports"] = [
            aid for aid in (export_artifact_ids or [])
        ]
        event = {
            "audit_event_id": "mrev_" + uuid4().hex[:16],
            "actor": actor,
            "action": action,
            "record_id": record_id,
            "new_record_id": new_record["record_id"] if new_record else record_id,
            "before": before,
            "after": deepcopy(current),
            "reason": reason,
            "evidence_ids": list(evidence_ids),
            "revision": new_revision,
            "at": _now(),
        }
        # 审计事件必须完整保留，禁止按条数截断；历史复核全程可追溯。
        events = state.setdefault("metric_review_events", [])
        events.append(event)
        save_business(db, task_id, state)
        db.execute("UPDATE tasks SET revision=? WHERE task_id=?", (new_revision, task_id))
        response = envelope(
            task_id, new_revision,
            {
                "action": action,
                "record": deepcopy(current),
                "audit_event_id": event["audit_event_id"],
                "invalidated": invalidated,
            },
        )
        db.execute(
            "INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
            (identity, task_id, TOOL, str(key), digest, json.dumps(response)),
        )

    # 仅在数据库提交成功后刷新页面镜像。这些镜像是 best-effort：
    # 真相源是已提交的 SQLite（revision 递增后旧导出按 revision 动态判为过期），
    # 刷新失败不得把已提交的成功业务改成 500，否则客户端会误判并错误重放。
    try:
        app.state.checks[task_id] = [
            c for c in state.get("checks", []) if c.get("is_current", True)
        ]
        app.state.comparisons_by_task[task_id] = [
            c for c in state.get("comparisons", []) if c.get("is_current", True)
        ]
        app.state.artifacts.invalidate_task(task_id)
    except Exception:
        # 重启后 create_app 会从 business_state 恢复镜像；导出过期由 revision 保证。
        pass
    return response


def audit_events(app, task_id: str, actor: str) -> list[dict]:
    """只读审计展示数据。

    任务不存在返回 404、归属不符返回 403，两种错误的 task_id/revision 均为 null，
    与契约「权限/存在性失败不泄露任务信息」一致。
    """
    with app.state.tasks.transaction() as db:
        row = db.execute("SELECT results FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            _fail("NOT_FOUND", 404, "task not found")
        results = json.loads(row[0])
        from .review_dispositions import can_review
        if not can_review(results, actor):
            _fail("FORBIDDEN", 403, "task owner mismatch")
        state = read_business(db, task_id)
    fields = (
        "audit_event_id", "actor", "action", "record_id", "new_record_id",
        "before", "after", "reason", "evidence_ids", "revision", "at",
    )
    return [
        {k: deepcopy(e.get(k)) for k in fields}
        for e in reversed(state.get("metric_review_events", []))
    ]
