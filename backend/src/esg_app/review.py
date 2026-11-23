"""人工复核与指标变更失效（Task 5）。

复核只走用户会话；Agent（模型）令牌无权复核。每次成功复核：
- 校验理由非空、expected_revision 匹配、目标检查项合法
- 原子写入检查项 + 递增任务 revision + 记录审计事件
- 旧评分标记 is_current=false（过期），需重算
核心指标变化使依赖它的检查项失效为 unknown / unreviewed / is_current=false。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .rules_registry import ALL_CHECK_IDS, METRIC_TO_CHECKS


class ReviewError(ValueError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


@dataclass
class ReviewCommand:
    action: str = "set_check_status"
    check_id: Optional[str] = None
    new_status: Optional[str] = None
    reason: str = ""
    expected_revision: int = 0
    target_key: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewStore:
    """内存复核仓储：任务、检查项、评分、审计事件。真实实现应换 SQLite 事务。"""

    def __init__(self) -> None:
        self._tasks: dict[str, dict] = {}
        self._checks: dict[str, dict[str, dict]] = {}
        self._scores: dict[str, dict] = {}
        self._events: dict[str, list[dict]] = {}

    def create_task(self, task_id: str, revision: int = 0, stage: str = "metrics") -> None:
        self._tasks[task_id] = {
            "task_id": task_id, "revision": revision, "stage": stage,
            "updated_at": _now(),
        }
        self._checks.setdefault(task_id, {})
        self._events.setdefault(task_id, [])

    def get_task(self, task_id: str) -> dict:
        if task_id not in self._tasks:
            raise ReviewError("TASK_NOT_FOUND")
        return dict(self._tasks[task_id])

    def put_score(self, task_id: str, score: dict) -> None:
        self._scores[task_id] = dict(score)

    def get_score(self, task_id: str) -> dict | None:
        return self._scores.get(task_id)

    def get_check(self, task_id: str, check_id: str) -> dict | None:
        return self._checks.get(task_id, {}).get(check_id)

    def put_check(self, task_id: str, check: dict) -> None:
        self._checks.setdefault(task_id, {})[check["check_id"]] = dict(check)

    def audit_events(self, task_id: str) -> list[dict]:
        return list(self._events.get(task_id, []))

    def _append_event(self, task_id: str, event: dict) -> None:
        self._events.setdefault(task_id, []).append(event)

    def bump_revision(self, task_id: str) -> int:
        task = self._tasks[task_id]
        task["revision"] += 1
        task["updated_at"] = _now()
        return task["revision"]

    def invalidate_evidence(self, task_id: str) -> None:
        """复核后比较/导出过期：本内存实现标记评分失效，比较失效由调用方处理。"""
        score = self._scores.get(task_id)
        if score is not None:
            score["is_current"] = False


def apply_review(store: ReviewStore, task_id: str, command: ReviewCommand,
                 actor: str, is_agent: bool = False) -> dict:
    """执行一条人工复核命令，返回更新后的 Task。"""
    if is_agent or (isinstance(actor, str) and actor.startswith("agent:")):
        raise ReviewError("AGENT_CANNOT_REVIEW", "模型/Agent 令牌无权人工复核")
    if not command.reason or not command.reason.strip():
        raise ReviewError("REASON_REQUIRED", "修改理由不能为空")

    task = store.get_task(task_id)
    if command.expected_revision != task["revision"]:
        raise ReviewError("REVISION_CONFLICT",
                          f"expected {command.expected_revision}, current {task['revision']}")

    if command.action in ("set_check_status", "set_na"):
        if command.check_id not in ALL_CHECK_IDS:
            raise ReviewError("UNKNOWN_CHECK")
        if command.action == "set_na":
            new_status = "not_applicable"
        else:
            if command.new_status not in ("met", "partial", "not_met", "unknown"):
                raise ReviewError("BAD_STATUS")
            new_status = command.new_status
        check = store.get_check(task_id, command.check_id) or {
            "check_id": command.check_id, "rule_version": "disclosure-v0.1",
            "evidence_ids": [], "missing_information": [], "suggestion": "",
            "target_key": command.target_key,
        }
        updated = {
            **check,
            "status": new_status,
            "origin": "manual",
            "review_state": "confirmed",
            "reason": command.reason,
            "revision": task["revision"] + 1,
            "is_current": True,
        }
        store.put_check(task_id, updated)
    else:
        raise ReviewError("UNKNOWN_ACTION")

    new_rev = store.bump_revision(task_id)
    store._tasks[task_id]["stage"] = "review"
    store.invalidate_evidence(task_id)
    store._append_event(task_id, {
        "actor": actor, "action": command.action, "check_id": command.check_id,
        "new_status": new_status, "reason": command.reason,
        "revision": new_rev, "at": _now(),
    })
    return store.get_task(task_id)


def invalidate_checks_for_metric(checks: dict[str, dict], metric_id: str,
                                 current_revision: int) -> dict[str, dict]:
    """核心指标变更后，使依赖它的检查项失效（不改原 dict，返回新 dict）。"""
    affected = METRIC_TO_CHECKS.get(metric_id, frozenset())
    out: dict[str, dict] = {}
    for cid, c in checks.items():
        if cid in affected:
            out[cid] = {
                **c,
                "status": "unknown",
                "review_state": "unreviewed",
                "is_current": False,
                "revision": current_revision,
                "reason": f"INVALIDATED_BY_METRIC_CHANGE:{metric_id} 变更后关联检查失效，需重新复核",
            }
        else:
            out[cid] = dict(c)
    return out
