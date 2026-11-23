"""业务写入边界：短事务提交、持久幂等、提取占用与实际调用记账。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator

from .comparison import compare_records
from .extraction import extract_metrics
from .metrics import validate_candidates
from .parse_repository import (
    read_business,
    save_business,
    token_identity,
    bound_reports,
)
from .parse_service import ParseError
from .review import invalidate_checks_for_metric
from .schemas import make_issue

SCHEMA = json.loads(Path(__file__).with_name("contract_schema.json").read_text())
WRITES = {"extract_metrics", "compare_years", "save_check_proposals", "export_analysis"}


def validate(name, value):
    schema = {"$ref": "#/$defs/" + name, "$defs": SCHEMA["$defs"]}
    return not any(Draft202012Validator(schema).iter_errors(value))


def envelope(task_id, revision, data):
    return dict(ok=True, data=data, error=None, task_id=task_id, revision=revision)


class WriteService:
    def __init__(self, app, evidence):
        self.app, self.repo, self.evidence = app, app.state.tasks, evidence

    def fail(self, code, status, message, task_id=None, revision=None):
        error = ParseError(code, status, message, task_id, revision)
        error.payload["error"]["retryable"] = code in {"MODEL_UNAVAILABLE", "TIMEOUT"}
        raise error

    def auth(self, db, task_id, token):
        row = db.execute(
            "SELECT task_id FROM tokens WHERE identity=?",
            (token_identity(token or ""),),
        ).fetchone()
        if not row:
            self.fail("UNAUTHORIZED", 401, "Missing or invalid task token")
        if row[0] != task_id:
            self.fail("FORBIDDEN", 403, "Token is not authorized for this task")
        row = db.execute(
            "SELECT revision,results FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            self.fail("FORBIDDEN", 403, "Task is not accessible")
        return row[0], json.loads(row[1])

    def replay(self, db, identity, task_id, tool, key, digest):
        row = db.execute(
            "SELECT request_hash,response FROM idempotency WHERE identity=? AND task_id=? AND tool=? AND key=?",
            (identity, task_id, tool, key),
        ).fetchone()
        if row:
            if row[0] != digest:
                self.fail(
                    "IDEMPOTENCY_CONFLICT",
                    409,
                    "Key was used for a different request",
                    task_id,
                )
            return json.loads(row[1])

    def revision(self, expected, actual, task_id):
        if expected != actual:
            self.fail(
                "REVISION_CONFLICT",
                409,
                "Task changed; read latest results",
                task_id,
                actual,
            )

    def run(self, task_id, token, tool, key, args):
        with self.repo.transaction() as db:
            revision, task = self.auth(db, task_id, token)
        # Task ID comes from the host route, never from model arguments.
        if (
            tool == "save_check_proposals"
            and isinstance(args, dict)
            and isinstance(args.get("checks"), list)
        ):
            if any(
                isinstance(c, dict)
                and c.get("rule_version") != self.app.state.rulebook["rule_version"]
                for c in args["checks"]
            ):
                self.fail(
                    "RULE_VERSION_MISMATCH",
                    409,
                    "Read the current rulebook",
                    task_id,
                    revision,
                )
        inp = dict(args, task_id=task_id) if isinstance(args, dict) else None
        if (
            not isinstance(args, dict)
            or "task_id" in args
            or not validate(tool + "_input", inp)
        ):
            self.fail(
                "INVALID_INPUT",
                422,
                "Arguments do not match the tool contract",
                task_id,
                revision,
            )
        try:
            UUID(key)
        except (ValueError, TypeError, AttributeError):
            self.fail(
                "INVALID_INPUT",
                422,
                "Host Idempotency-Key must be a UUID",
                task_id,
                revision,
            )
        identity = token_identity(token)
        digest = hashlib.sha256(
            json.dumps(args, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if tool == "export_analysis":
            return self.app.state.export_service.run(
                task_id, identity, key, args, token=token
            )
        attempt = None
        evidence = self.evidence(task_id) if tool == "extract_metrics" else []
        deps = self.app.state.extraction_deps
        fingerprint = hashlib.sha256(
            json.dumps(
                dict(
                    evidence=[e.to_dict() for e in evidence],
                    metrics=sorted(args.get("metric_ids", [])),
                    years=sorted(args.get("years", [])),
                    extractor=deps.extractor_version,
                    model=deps.model_id,
                    prompt=deps.prompt_version,
                ),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with self.repo.transaction() as db:
            revision, task = self.auth(db, task_id, token)
            cached = self.replay(db, identity, task_id, tool, key, digest)
            if cached:
                return cached
            self.revision(args["expected_revision"], revision, task_id)
            state = read_business(db, task_id)
            if tool == "extract_metrics":
                selected = task.get(
                    "selected_years", task.get("task", {}).get("selected_years", [])
                )
                if not set(args["years"]) <= set(selected):
                    self.fail(
                        "INVALID_YEAR",
                        422,
                        "Years must be selected on the task",
                        task_id,
                        revision,
                    )
                reports = bound_reports(task)
                if not reports:
                    self.fail(
                        "INVALID_STATE",
                        422,
                        "Task has no bound reports",
                        task_id,
                        revision,
                    )
                cache = state.get("extraction_cache", {}).get(fingerprint)
                current_records = {
                    r["record_id"]: r for r in state["records"] if r.get("is_current")
                }
                if cache and all(
                    current_records.get(r["record_id"]) == r for r in cache["records"]
                ):
                    cache = json.loads(json.dumps(cache))
                    cache["run"].update(
                        run_id="run_" + uuid4().hex,
                        usage=dict(model_calls=0, input_tokens=0, output_tokens=0),
                    )
                    state["runs"].append(cache["run"])
                    self.check_capacity(state, task_id, revision)
                    response = envelope(task_id, revision, cache)
                    save_business(db, task_id, state)
                    db.execute(
                        "INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
                        (identity, task_id, tool, key, digest, json.dumps(response)),
                    )
                    return response
                used = db.execute(
                    "SELECT COALESCE(SUM(model_calls),0) FROM extraction_attempts WHERE task_id=?",
                    (task_id,),
                ).fetchone()[0]
                if used >= self.app.state.settings.max_extraction_model_calls:
                    self.fail(
                        "BUDGET_EXCEEDED",
                        429,
                        "Backend extraction call limit reached",
                        task_id,
                        revision,
                    )
                if db.execute(
                    "SELECT 1 FROM extraction_attempts WHERE task_id=? AND status='running'",
                    (task_id,),
                ).fetchone():
                    self.fail(
                        "INVALID_STATE",
                        422,
                        "Extraction is in progress; replay the original request later",
                        task_id,
                        revision,
                    )
                deps = self.app.state.extraction_deps
                if deps.model_fn is None:
                    self.fail(
                        "MODEL_UNAVAILABLE",
                        503,
                        "Backend extractor is not configured",
                        task_id,
                        revision,
                    )
                attempt = "attempt_" + uuid4().hex
                # Reservation survives worker loss: recovery must reconcile uncertain calls,
                # never automatically repeat a possibly billed extraction.
                db.execute(
                    "INSERT INTO extraction_attempts VALUES(?,?,?,?,?,?,?)",
                    (attempt, task_id, identity, key, digest, "running", 0),
                )
        if tool != "extract_metrics":
            return self.commit(task_id, token, tool, key, digest, args)
        try:
            allowed = {e.evidence_id: e.report_id for e in evidence}
            evidence_by_id = {e.evidence_id: e for e in evidence}
            confirmed = {
                (r["metric_id"], r["metric_year"])
                for r in state["records"]
                if r.get("is_current")
                and (
                    r.get("review_state") == "confirmed" or r.get("origin") == "manual"
                )
            }
            with self.repo.transaction() as db:
                current, latest = self.auth(db, task_id, token)
                self.revision(revision, current, task_id)
                if latest != task:
                    self.fail(
                        "REVISION_CONFLICT",
                        409,
                        "Task binding changed",
                        task_id,
                        current,
                    )
                db.execute(
                    "UPDATE extraction_attempts SET model_calls=1 WHERE attempt_id=?",
                    (attempt,),
                )
            result = extract_metrics(
                task_id,
                revision,
                revision,
                args["metric_ids"],
                args["years"],
                set(allowed),
                deps,
                confirmed_record_keys=confirmed,
            ).to_dict()
            if any(i["code"] == "EVIDENCE_FOREIGN" for i in result["issues"]):
                self.fail(
                    "FOREIGN_EVIDENCE",
                    403,
                    "Extraction references evidence outside task scope",
                    task_id,
                    revision,
                )
            accepted = []
            issues = result["issues"]
            for r in result["records"]:
                if (
                    not validate("MetricRecord", r)
                    or r["report_id"] not in reports
                    or any(
                        allowed.get(eid) != r["report_id"] for eid in r["evidence_ids"]
                    )
                    or any(
                        evidence_by_id[eid].column_year is not None
                        and evidence_by_id[eid].column_year != r["metric_year"]
                        for eid in r["evidence_ids"]
                        if eid in evidence_by_id
                    )
                    or r["origin"] == "derived"
                    or r.get("supersedes_id") is not None
                ):
                    issues.append(
                        make_issue(
                            "INVALID_CANDIDATE",
                            "Candidate cannot be verified against task evidence",
                        )
                    )
                else:
                    # Model boundary labels cannot act as server confirmation.
                    r["boundary_key"] = None
                    accepted.append(r)
            for r in state["records"]:
                if (
                    (r["metric_id"], r["metric_year"]) in confirmed
                    and r.get("is_current")
                    and r["metric_id"] in args["metric_ids"]
                    and r["metric_year"] in args["years"]
                ):
                    accepted.append(r)
            covered = {(r["metric_id"], r["metric_year"]) for r in accepted}
            for metric in args["metric_ids"]:
                for year in args["years"]:
                    if (metric, year) not in covered:
                        accepted.append(
                            self.unknown(
                                task_id, reports[0], metric, year, revision + 1
                            )
                        )
            for metric in args["metric_ids"]:
                for year in args["years"]:
                    same = [
                        r
                        for r in accepted
                        if r["metric_id"] == metric
                        and r["metric_year"] == year
                        and r["review_state"] != "confirmed"
                    ]
                    if (
                        len(
                            {
                                (r["value"], r["canonical_unit"], r["boundary_text"])
                                for r in same
                            }
                        )
                        > 1
                    ):
                        for r in same:
                            r["status"] = "conflict"
                            r["extraction_note"] = (
                                "Multiple conflicting candidates require human review"
                            )
            result["records"] = accepted
            result["status"] = (
                "partial"
                if any(
                    r["status"] not in ("extracted", "missing_marker") for r in accepted
                )
                else "completed"
            )
            result["issues"] = issues[:100]
            result["run"]["record_ids"] = [r["record_id"] for r in accepted]
            return self.commit(
                task_id,
                token,
                tool,
                key,
                digest,
                args,
                result,
                attempt,
                fingerprint,
                task,
            )
        except BaseException as exc:
            with self.repo.transaction() as db:
                db.execute(
                    "UPDATE extraction_attempts SET status='failed' WHERE attempt_id=? AND status='running'",
                    (attempt,),
                )
            if isinstance(exc, (ParseError, KeyboardInterrupt, SystemExit)):
                raise
            self.fail(
                "TIMEOUT" if isinstance(exc, TimeoutError) else "MODEL_UNAVAILABLE",
                504 if isinstance(exc, TimeoutError) else 503,
                "Backend extraction failed",
                task_id,
                revision,
            )

    @staticmethod
    def unknown(task_id, report_id, metric, year, revision):
        return dict(
            report_id=report_id,
            metric_id=metric,
            metric_year=year,
            raw_value=None,
            value=None,
            raw_unit=None,
            canonical_unit=None,
            conversion_rule=None,
            denominator=None,
            boundary_text="",
            boundary_key=None,
            method_text="",
            scope3_categories=[],
            evidence_ids=[],
            status="unknown",
            origin="reported",
            supersedes_id=None,
            derivation=None,
            extraction_note="No verifiable candidate for this metric and year",
            record_id="mr_" + uuid4().hex,
            task_id=task_id,
            review_state="unreviewed",
            revision=revision,
            is_current=True,
        )

    def commit(
        self,
        task_id,
        token,
        tool,
        key,
        digest,
        args,
        result=None,
        attempt=None,
        fingerprint=None,
        snapshot=None,
    ):
        # Evidence loading can use repository transactions; do it before the write lock.
        evidence_ids = {e.evidence_id for e in self.evidence(task_id)}
        with self.repo.transaction() as db:
            revision, task = self.auth(db, task_id, token)
            replay = self.replay(db, token_identity(token), task_id, tool, key, digest)
            if replay:
                return replay
            self.revision(args["expected_revision"], revision, task_id)
            if snapshot is not None and task != snapshot:
                self.fail(
                    "REVISION_CONFLICT", 409, "Task binding changed", task_id, revision
                )
            state = read_business(db, task_id)
            new_revision = revision + 1
            if tool == "extract_metrics":
                requested = {(m, y) for m in args["metric_ids"] for y in args["years"]}
                # Keep history and leave human records untouched.
                retained_ids = {r["record_id"] for r in result["records"]}
                for r in state["records"]:
                    if (r["metric_id"], r["metric_year"]) in requested and r[
                        "record_id"
                    ] not in retained_ids:
                        r["is_current"] = False
                existing = {r["record_id"] for r in state["records"]}
                for r in result["records"]:
                    if r["record_id"] not in existing:
                        r["revision"] = new_revision
                        state["records"].append(r)
                state["runs"].append(result["run"])
                state.setdefault("extraction_cache", {})[fingerprint] = result
                state["issues"] = result["issues"]
                checks = {c["check_id"]: c for c in state["checks"]}
                for metric in args["metric_ids"]:
                    checks = invalidate_checks_for_metric(checks, metric, new_revision)
                state["checks"] = list(checks.values())
            elif tool == "compare_years":
                by_id = {
                    r["record_id"]: r for r in state["records"] if r.get("is_current")
                }
                older, newer = by_id.get(args["older_record_id"]), by_id.get(
                    args["newer_record_id"]
                )
                if not older or not newer:
                    self.fail(
                        "NOT_FOUND",
                        404,
                        "Current record not found in task scope",
                        task_id,
                        revision,
                    )
                if (
                    older["metric_id"] != newer["metric_id"]
                    or older["metric_year"] >= newer["metric_year"]
                ):
                    self.fail(
                        "INVALID_INPUT",
                        422,
                        "Records must share a metric with increasing years",
                        task_id,
                        revision,
                    )
                same_company = older["report_id"] == newer["report_id"]
                if not same_company:
                    reports_by_id = {r["report_id"]: r for r in task.get("reports", [])}
                    old_company = reports_by_id.get(older["report_id"], {}).get(
                        "company_id"
                    )
                    new_company = reports_by_id.get(newer["report_id"], {}).get(
                        "company_id"
                    )
                    same_company = bool(old_company and old_company == new_company)
                if not same_company:
                    self.fail(
                        "INVALID_INPUT",
                        422,
                        "Report companies are not confirmed equal",
                        task_id,
                        revision,
                    )
                result = next(
                    (
                        c
                        for c in state["comparisons"]
                        if c["is_current"]
                        and c["older_record_id"] == older["record_id"]
                        and c["newer_record_id"] == newer["record_id"]
                    ),
                    None,
                )
                if result:
                    new_revision = revision
                else:
                    result = compare_records(older, newer, same_company=same_company)
                    result["revision"] = new_revision
                    state["comparisons"].append(result)
            else:
                checks = {c["check_id"]: c for c in state["checks"]}
                rules = {r["check_id"]: r for r in self.app.state.rulebook["checks"]}
                saved = []
                if len({c["check_id"] for c in args["checks"]}) != len(args["checks"]):
                    self.fail(
                        "INVALID_INPUT", 422, "Duplicate check IDs", task_id, revision
                    )
                for c in args["checks"]:
                    if not set(c["evidence_ids"]) <= evidence_ids:
                        self.fail(
                            "FOREIGN_EVIDENCE",
                            403,
                            "Evidence is outside task scope",
                            task_id,
                            revision,
                        )
                    if (
                        c["status"] == "partial"
                        and not rules[c["check_id"]]["partial_anchor"]
                    ):
                        self.fail(
                            "INVALID_INPUT",
                            422,
                            "Rule does not allow partial status",
                            task_id,
                            revision,
                        )
                    old = checks.get(c["check_id"])
                    if old and (
                        old.get("origin") == "manual"
                        or old.get("review_state") == "confirmed"
                    ):
                        if any(old.get(k) != v for k, v in c.items()):
                            self.fail(
                                "INVALID_STATE",
                                422,
                                "Cannot overwrite a human decision",
                                task_id,
                                revision,
                            )
                        saved.append(old)
                        continue
                    check = dict(
                        c,
                        task_id=task_id,
                        origin="model",
                        review_state="unreviewed",
                        revision=new_revision,
                        is_current=True,
                    )
                    saved.append(check)
                    checks[c["check_id"]] = check
                target_checks = [
                    c
                    for c in checks.values()
                    if c["check_id"].startswith("T")
                    and c.get("is_current")
                    and c["status"] != "unknown"
                ]
                if target_checks and (
                    any(not c.get("target_key") for c in target_checks)
                    or len({c["target_key"] for c in target_checks}) != 1
                ):
                    self.fail(
                        "INVALID_STATE",
                        422,
                        "Target checks must refer to the same selected target",
                        task_id,
                        revision,
                    )
                state["checks"] = list(checks.values())
                result = dict(checks=saved, issues=[])
            if new_revision != revision:
                state.pop("score", None)
            if tool != "compare_years":
                for c in state["comparisons"]:
                    c["is_current"] = False
            self.check_capacity(state, task_id, revision)
            response = envelope(task_id, new_revision, result)
            if not validate(tool + "_output", response):
                self.fail(
                    "INTERNAL_ERROR",
                    500,
                    "Generated result violates the output contract",
                    task_id,
                    revision,
                )
            save_business(db, task_id, state)
            db.execute(
                "UPDATE tasks SET revision=? WHERE task_id=?", (new_revision, task_id)
            )
            db.execute(
                "INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
                (
                    token_identity(token),
                    task_id,
                    tool,
                    key,
                    digest,
                    json.dumps(response),
                ),
            )
            if attempt:
                db.execute(
                    "UPDATE extraction_attempts SET status='completed' WHERE attempt_id=?",
                    (attempt,),
                )
        self.app.state.checks[task_id] = state["checks"]
        self.app.state.comparisons_by_task[task_id] = state["comparisons"]
        return response

    def read(self, task_id, token, tool, args):
        with self.repo.transaction() as db:
            revision, _ = self.auth(db, task_id, token)
        if (
            not isinstance(args, dict)
            or "task_id" in args
            or not validate(tool + "_input", dict(args, task_id=task_id))
        ):
            self.fail(
                "INVALID_INPUT",
                422,
                "Arguments do not match the tool contract",
                task_id,
                revision,
            )
        evidence = {e.evidence_id: e for e in self.evidence(task_id)}
        with self.repo.transaction() as db:
            revision, task = self.auth(db, task_id, token)
            state = read_business(db, task_id)
            if tool == "calculate_score":
                from .scoring import score_checks

                checks = state["checks"] or self.app.state.checks.get(task_id, [])
                result = score_checks(checks, self.app.state.rulebook)
                result["revision"] = revision
                state["score"] = result
                save_business(db, task_id, state)
            elif tool == "validate_metrics":
                by_id = {r["record_id"]: r for r in state["records"]}
                if not all(rid in by_id for rid in args["record_ids"]):
                    self.fail(
                        "NOT_FOUND",
                        404,
                        "Record not found in task scope",
                        task_id,
                        revision,
                    )
                issues, invalid = [], False
                for rid in args["record_ids"]:
                    r = by_id[rid]
                    if (
                        not validate("MetricRecord", r)
                        or not set(r.get("evidence_ids", [])) <= set(evidence)
                        or r.get("report_id") not in bound_reports(task)
                        or not validate_candidates(
                            [r],
                            task.get("task", task).get("selected_years", []),
                            set(evidence),
                        ).accepted
                        or any(
                            evidence[eid].report_id != r["report_id"]
                            or (
                                evidence[eid].column_year is not None
                                and evidence[eid].column_year != r["metric_year"]
                            )
                            for eid in r.get("evidence_ids", [])
                            if eid in evidence
                        )
                    ):
                        issues.append(
                            make_issue(
                                "INVALID_RECORD",
                                "Record structure or evidence cannot be verified",
                                record_id=rid,
                            )
                        )
                        invalid = True
                    elif (
                        not r.get("is_current")
                        or r.get("review_state") != "confirmed"
                        or r["status"] not in ("extracted", "missing_marker")
                    ):
                        issues.append(
                            make_issue(
                                "NEEDS_REVIEW",
                                "Record is stale, unresolved or not reviewed",
                                record_id=rid,
                            )
                        )
                result = dict(
                    status=(
                        "invalid" if invalid else "needs_review" if issues else "valid"
                    ),
                    checked_record_ids=args["record_ids"],
                    issues=issues,
                )
            else:
                result = self.app.state.rulebook
            response = envelope(task_id, revision, result)
            if not validate(tool + "_output", response):
                self.fail(
                    "INTERNAL_ERROR", 500, "Invalid generated result", task_id, revision
                )
            return response

    def check_capacity(self, state, task_id, revision):
        if any(
            len(state[k]) > limit
            for k, limit in (("records", 500), ("comparisons", 100), ("runs", 100))
        ):
            self.fail(
                "INVALID_STATE",
                422,
                "Task history reached the result contract limit",
                task_id,
                revision,
            )
