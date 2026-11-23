"""先生成未公开文件，再在版本检查事务中发布整包元数据。"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from .exports import ArtifactStore, build_export_bundle
from .parse_repository import read_business, bound_reports
from .parse_service import ParseError
from .scoring import score_checks
from .write_service import envelope, validate


class ExportService:
    def __init__(self, app, evidence):
        self.app, self.repo, self.evidence = app, app.state.tasks, evidence

    def run(self, task_id, identity, key, args, token=None):
        # User calls arrive only from the authenticated local user route. Agent calls
        # must recheck their task token at both snapshot and publication boundaries.
        def snapshot(db):
            if token is not None:
                return self.app.state.write_service.auth(db, task_id, token)
            row = db.execute(
                "SELECT revision,results FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise ParseError("NOT_FOUND", 404, "Task not found")
            return row[0], json.loads(row[1])

        with self.repo.transaction() as db:
            revision, task = snapshot(db)
        if (
            not isinstance(args, dict)
            or "task_id" in args
            or not validate("export_analysis_input", dict(args, task_id=task_id))
        ):
            raise ParseError(
                "INVALID_INPUT", 422, "Invalid export arguments", task_id, revision
            )
        try:
            UUID(key)
        except (ValueError, TypeError, AttributeError):
            raise ParseError(
                "INVALID_INPUT",
                422,
                "Host Idempotency-Key must be a UUID",
                task_id,
                revision,
            )
        digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        ws = self.app.state.write_service
        with self.repo.transaction() as db:
            revision, task = snapshot(db)
            old = ws.replay(db, identity, task_id, "export_analysis", key, digest)
            if old:
                return old
            ws.revision(args["expected_revision"], revision, task_id)
            state = read_business(db, task_id)
            # Compatibility for trusted host-seeded demo checks; persisted state wins.
            checks = (
                state["checks"]
                if state["checks"]
                else self.app.state.checks.get(task_id, [])
            )
        evidence = [e.to_dict() for e in self.evidence(task_id)]
        root = self.app.state.artifacts.root
        staging = Path(tempfile.mkdtemp(prefix="bundle-", dir=root))
        published = False
        moved = []
        try:
            staged = ArtifactStore(staging)
            score = score_checks(checks, self.app.state.rulebook)
            score["revision"] = revision
            bundle = build_export_bundle(
                task_id,
                revision,
                revision,
                [r for r in state["records"] if r.get("is_current")],
                score,
                [c for c in checks if c.get("is_current", True)],
                [c for c in state["comparisons"] if c.get("is_current")],
                evidence,
                self.app.state.rulebook["rule_version"],
                [
                    r
                    for r in task.get("reports", [])
                    if r.get("report_id") in bound_reports(task)
                ],
                staged,
            )
            response = envelope(task_id, revision, bundle)
            if not validate("export_analysis_output", response):
                raise ParseError(
                    "INTERNAL_ERROR",
                    500,
                    "Invalid generated export bundle",
                    task_id,
                    revision,
                )
            with self.repo.transaction() as db:
                current, latest_task = snapshot(db)
                old = ws.replay(db, identity, task_id, "export_analysis", key, digest)
                if old:
                    return old
                ws.revision(revision, current, task_id)
                if latest_task != task:
                    raise ParseError(
                        "REVISION_CONFLICT",
                        409,
                        "Task binding changed",
                        task_id,
                        current,
                    )
                for meta in staged._meta.values():
                    destination = root / meta["filename"]
                    Path(meta["_path"]).rename(destination)
                    moved.append(destination)
                    meta["_path"] = str(destination.resolve())
                    meta["user_identity"] = (
                        identity if identity.startswith("user:") else "user:local_user"
                    )
                    db.execute(
                        "INSERT INTO artifacts VALUES(?,?,?)",
                        (meta["artifact_id"], task_id, json.dumps(meta)),
                    )
                db.execute(
                    "INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
                    (
                        identity,
                        task_id,
                        "export_analysis",
                        key,
                        digest,
                        json.dumps(response),
                    ),
                )
            published = True
            return response
        finally:
            shutil.rmtree(staging)
            if not published:
                for path in moved:
                    path.unlink(missing_ok=True)
