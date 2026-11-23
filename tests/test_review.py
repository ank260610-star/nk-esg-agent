"""Task 5: 人工复核测试。

handoff §4 + Task 5 验收项：
- 模型令牌不能人工复核（Agent 身份无权）
- 修改理由不能为空
- 旧 revision 返回 409
- 修改后旧评分/导出过期（is_current=false / revision 递增）
- 核心指标修改后其关联检查及比较失效为 unknown / needs_review
- 原子复核 + 审计记录（review_events）
"""
import sys
from pathlib import Path
import copy
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.review import (
    ReviewCommand, ReviewError, apply_review, ReviewStore,
    invalidate_checks_for_metric,
)


def make_store(revision=7):
    s = ReviewStore()
    s.create_task("task_rv", revision=revision)
    return s


def cmd(action="set_check_status", check_id="C01", new_status="met",
        reason="人工核对 p127 原表后确认满足。", expected_revision=7, **kw):
    return ReviewCommand(action=action, check_id=check_id, new_status=new_status,
                         reason=reason, expected_revision=expected_revision, **kw)


class TestApplyReview:
    def test_model_actor_rejected(self):
        store = make_store()
        with pytest.raises(ReviewError) as ei:
            apply_review(store, "task_rv", cmd(), actor="agent:harness", is_agent=True)
        assert "AGENT_CANNOT_REVIEW" in str(ei.value)

    def test_empty_reason_rejected(self):
        store = make_store()
        with pytest.raises(ReviewError) as ei:
            apply_review(store, "task_rv", cmd(reason="   "), actor="user_zhang")
        assert "REASON_REQUIRED" in str(ei.value)

    def test_stale_revision_rejected(self):
        store = make_store(revision=8)
        with pytest.raises(ReviewError) as ei:
            apply_review(store, "task_rv", cmd(expected_revision=7), actor="user_zhang")
        assert "REVISION_CONFLICT" in str(ei.value)

    def test_successful_review_increments_revision(self):
        store = make_store()
        before = store.get_task("task_rv")["revision"]
        task = apply_review(store, "task_rv", cmd(), actor="user_zhang")
        assert task["revision"] == before + 1
        assert task["stage"] == "review"

    def test_review_writes_check_and_audit(self):
        store = make_store()
        apply_review(store, "task_rv", cmd(), actor="user_zhang")
        check = store.get_check("task_rv", "C01")
        assert check["status"] == "met"
        assert check["origin"] == "manual"
        assert check["review_state"] == "confirmed"
        events = store.audit_events("task_rv")
        assert len(events) >= 1
        assert events[-1]["actor"] == "user_zhang"
        assert events[-1]["action"] == "set_check_status"

    def test_old_score_marked_stale_after_review(self):
        store = make_store()
        store.put_score("task_rv", {"revision": 7, "is_current": True, "label": "insufficient_evidence"})
        apply_review(store, "task_rv", cmd(), actor="user_zhang")
        score = store.get_score("task_rv")
        assert score["is_current"] is False, "复核后旧评分必须失效"

    def test_review_na_requires_reason_and_checks_scope(self):
        store = make_store()
        with pytest.raises(ReviewError):
            apply_review(store, "task_rv", cmd(action="set_na", check_id="C01",
                                               new_status="not_applicable", reason=""),
                         actor="user_zhang")
        # 理由充分时允许人工 N/A
        task = apply_review(store, "task_rv",
                            cmd(action="set_na", check_id="C01", new_status="not_applicable",
                                reason="已检查报告正文与索引， Scope 3 类别未披露。"),
                            actor="user_zhang")
        assert store.get_check("task_rv", "C01")["status"] == "not_applicable"

    def test_unknown_check_id_rejected(self):
        store = make_store()
        with pytest.raises(ReviewError) as ei:
            apply_review(store, "task_rv", cmd(check_id="X99"), actor="user_zhang")
        assert "UNKNOWN_CHECK" in str(ei.value)

    def test_agent_token_field_rejected_even_if_flag_missing(self):
        """即便 is_agent 未传，actor 形如 agent:* 也必须拒绝。"""
        store = make_store()
        with pytest.raises(ReviewError) as ei:
            apply_review(store, "task_rv", cmd(), actor="agent:tool-call")
        assert "AGENT_CANNOT_REVIEW" in str(ei.value)


class TestMetricChangeInvalidation:
    def test_metric_change_invalidates_related_checks(self):
        """核心指标（E01/E02/E04/E05/E06）修改后关联检查失效为 unknown。"""
        checks = {
            "C01": {"check_id": "C01", "status": "met", "review_state": "confirmed",
                    "origin": "manual", "revision": 7, "is_current": True},
            "Q01": {"check_id": "Q01", "status": "met", "review_state": "confirmed",
                    "origin": "manual", "revision": 7, "is_current": True},
        }
        changed = invalidate_checks_for_metric(checks, "E01", current_revision=8)
        # C01 依赖 E01（完整性），应失效
        assert changed["C01"]["status"] == "unknown"
        assert changed["C01"]["is_current"] is False
        assert changed["C01"]["review_state"] == "unreviewed"
        assert "E01" in changed["C01"]["reason"]
        # Q01（数值可解释性）也依赖 E01 的值，同样失效
        assert changed["Q01"]["status"] == "unknown"
        assert "E01" in changed["Q01"]["reason"]

    def test_invalidation_keeps_audit_trail(self):
        checks = {"C01": {"check_id": "C01", "status": "met", "review_state": "confirmed",
                          "origin": "manual", "revision": 7, "is_current": True}}
        # C01 依赖 E01，改 E01 应留下失效原因
        changed = invalidate_checks_for_metric(checks, "E01", current_revision=9)
        assert "INVALIDATED_BY_METRIC_CHANGE" in changed["C01"].get("reason", "") or \
               "E01" in changed["C01"].get("reason", "")
        # E04 不影响 C01（C01 只看 E01/E02）
        unchanged = invalidate_checks_for_metric(checks, "E04", current_revision=9)
        assert unchanged["C01"]["status"] == "met"

    def test_no_change_for_unrelated_metric(self):
        checks = {"C01": {"check_id": "C01", "status": "met", "review_state": "confirmed",
                          "origin": "manual", "revision": 7, "is_current": True}}
        # T01 目标依赖 E01，改 E06 不影响 C01（C01 只看 E01/E02）
        changed = invalidate_checks_for_metric(checks, "E06", current_revision=8)
        assert changed["C01"]["status"] == "met"
