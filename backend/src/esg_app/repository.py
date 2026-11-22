"""任务与令牌内存仓储（Task 1 最小实现）。

真实实现须换为 SQLite + 事务 + revision 递增；此实现仅用于闸门 A 的
权限隔离与契约信封验证，模拟数据不是生产证据。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskRecord:
    task_id: str
    revision: int
    results: dict
    permission: str = "read-only"


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._agent_tokens: dict[str, str] = {}

    def register_task(self, task_id: str, revision: int, results: dict) -> None:
        self._tasks[task_id] = TaskRecord(task_id=task_id, revision=revision, results=results)

    def issue_agent_token(self, task_id: str, token: str) -> None:
        self._agent_tokens[token] = task_id

    def task_for_agent_token(self, token: str | None) -> str | None:
        return self._agent_tokens.get(token or "")

    def get(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def permission_for(self, task_id: str) -> str:
        rec = self._tasks.get(task_id)
        return rec.permission if rec else "none"
