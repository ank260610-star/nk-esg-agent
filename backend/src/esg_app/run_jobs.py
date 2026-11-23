"""AgentRun 状态机：SDK 异常/无结果绝不标 completed。"""
from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from enum import Enum


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    COMPLETED = "completed"
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
    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}

    def start(self, task_id: str, session_id: str) -> AgentRun:
        run = AgentRun(run_id=uuid.uuid4().hex, task_id=task_id, session_id=session_id,
                       status=RunStatus.RUNNING)
        self._runs[run.run_id] = run
        return run

    def complete(self, run_id: str, answer: str, revision: int | None) -> None:
        """只有拿到真实结构化工具结果才允许 completed。"""
        if not answer:
            raise ValueError("empty answer cannot be completed")
        run = self._runs[run_id]
        run.answer = answer
        run.revision = revision
        run.status = RunStatus.COMPLETED

    def fail(self, run_id: str, error: str) -> None:
        run = self._runs[run_id]
        run.error = error
        run.status = RunStatus.FAILED

    def cancel(self, run_id: str) -> None:
        self._runs[run_id].status = RunStatus.CANCELLED

    def get(self, run_id: str) -> AgentRun:
        return self._runs[run_id]

    def list_for_task(self, task_id: str) -> list[AgentRun]:
        return [r for r in self._runs.values() if r.task_id == task_id]

    def create_queued(self, task_id: str, session_id: str) -> AgentRun:
        """先落 queued，再由后台线程推进；防止阻塞 HTTP 事件循环。"""
        import uuid
        run = AgentRun(run_id=uuid.uuid4().hex, task_id=task_id,
                       session_id=session_id, status=RunStatus.QUEUED)
        self._runs[run.run_id] = run
        return run

    def mark_running(self, run_id: str) -> None:
        self._runs[run_id].status = RunStatus.RUNNING
