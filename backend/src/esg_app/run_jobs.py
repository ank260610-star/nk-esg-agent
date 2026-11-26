"""AgentRun 状态机：SDK 异常/无结果绝不标 completed。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, asdict
from enum import Enum


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    COMPLETED = "completed"
    COMPLETED_WITH_LIMITATIONS = "completed_with_limitations"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentRun:
    run_id: str
    task_id: str
    session_id: str
    status: RunStatus = RunStatus.QUEUED
    error: str | None = None
    answer: str | None = None
    revision: int | None = None


class AgentRunStore:
    def __init__(self, repository=None) -> None:
        self.repository = repository
        self._runs: dict[str, AgentRun] = {}

    def save(self, run: AgentRun) -> None:
        self._runs[run.run_id] = run
        if self.repository is not None:
            with self.repository.transaction() as db:
                if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                    owner = db.execute(
                        "SELECT run_id FROM agent_run_claims WHERE task_id=?",
                        (run.task_id,),
                    ).fetchone()
                    if owner and owner[0] != run.run_id:
                        raise RuntimeError("Task already has an active agent run")
                    db.execute(
                        "INSERT OR IGNORE INTO agent_run_claims VALUES(?,?)",
                        (run.task_id, run.run_id),
                    )
                else:
                    db.execute(
                        "DELETE FROM agent_run_claims WHERE task_id=? AND run_id=?",
                        (run.task_id, run.run_id),
                    )
                db.execute(
                    "INSERT INTO agent_runs VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET state=excluded.state",
                    (run.run_id, run.task_id, json.dumps(asdict(run))),
                )

    def start(self, task_id: str, session_id: str) -> AgentRun:
        run = AgentRun(
            run_id=uuid.uuid4().hex,
            task_id=task_id,
            session_id=session_id,
            status=RunStatus.RUNNING,
        )
        self.save(run)
        return run

    def complete(self, run_id: str, answer: str, revision: int | None, *, with_limitations: bool = False) -> None:
        """只有拿到真实结构化工具结果才允许 completed。"""
        if not answer:
            raise ValueError("empty answer cannot be completed")
        run = self.get(run_id)
        run.answer = answer
        run.revision = revision
        run.status = RunStatus.COMPLETED_WITH_LIMITATIONS if with_limitations else RunStatus.COMPLETED
        self.save(run)

    def fail(self, run_id: str, error: str) -> None:
        run = self.get(run_id)
        run.error = error
        run.status = RunStatus.FAILED
        self.save(run)

    def cancel(self, run_id: str) -> None:
        run = self.get(run_id)
        run.status = RunStatus.CANCELLED
        self.save(run)

    def get(self, run_id: str) -> AgentRun:
        if self.repository is not None:
            with self.repository.transaction() as db:
                row = db.execute(
                    "SELECT state FROM agent_runs WHERE run_id=?", (run_id,)
                ).fetchone()
            if row is None:
                raise KeyError(run_id)
            raw = json.loads(row[0])
            raw["status"] = RunStatus(raw["status"])
            self._runs[run_id] = AgentRun(**raw)
        return self._runs[run_id]

    def list_for_task(self, task_id: str) -> list[AgentRun]:
        if self.repository is not None:
            with self.repository.transaction() as db:
                ids = [
                    row[0]
                    for row in db.execute(
                        "SELECT run_id FROM agent_runs WHERE task_id=? ORDER BY rowid",
                        (task_id,),
                    )
                ]
            return [self.get(run_id) for run_id in ids]
        return [r for r in self._runs.values() if r.task_id == task_id]

    def create_queued(self, task_id: str, session_id: str) -> AgentRun:
        """先落 queued，再由后台线程推进；防止阻塞 HTTP 事件循环。"""
        import uuid

        run = AgentRun(
            run_id=uuid.uuid4().hex,
            task_id=task_id,
            session_id=session_id,
            status=RunStatus.QUEUED,
        )
        self.save(run)
        return run

    def mark_running(self, run_id: str) -> None:
        run = self.get(run_id)
        run.status = RunStatus.RUNNING
        self.save(run)
