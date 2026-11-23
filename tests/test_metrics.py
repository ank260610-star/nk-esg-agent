"""Task 3: 后端指标提取与内部校验边界测试。

按 handoff Task 3 验收项：年份越界、错年/非法单位不入库、外任务证据拒绝、
Scope3缺失保留、冲突候选保留、幂等、revision 变化拒绝、人工确认保护、
未知记录可追踪。用离线替身模型验证，不依赖真实模型。
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.metrics import (
    validate_candidates,
    CandidateValidation,
    VALID_METRIC_IDS,
)
from esg_app.extraction import (
    extract_metrics,
    ExtractionDeps,
)


def candidate(metric_id="E01", year=2024, value="100.00", raw_unit="吨二氧化碳当量",
              status="extracted", evidence_ids=None, report_id="byd_report",
              origin="reported", **kw):
    """构造符合契约的 MetricCandidate 字典。"""
    d = {
        "report_id": report_id, "metric_id": metric_id, "metric_year": year,
        "raw_value": value, "value": value, "raw_unit": raw_unit,
        "canonical_unit": raw_unit, "conversion_rule": None,
        "denominator": None, "boundary_text": "", "boundary_key": None,
        "method_text": "", "scope3_categories": [],
        "evidence_ids": evidence_ids if evidence_ids is not None else ["ev_byd_p127"],
        "status": status, "origin": origin, "supersedes_id": None,
        "derivation": None, "extraction_note": kw.get("extraction_note", ""),
    }
    d.update({k: v for k, v in kw.items() if k != "extraction_note"})
    return d


class TestValidateCandidatesYearBoundary:
    def test_year_out_of_task_range_rejected(self):
        """年份不在任务内 → 候选被拒，不能入库。"""
        cands = [candidate(year=1999)]  # 任务年份只含 2024
        result = validate_candidates(
            candidates=cands, allowed_years=[2024],
            allowed_evidence_ids={"ev_byd_p127"},
        )
        assert len(result.accepted) == 0
        assert any(i["code"] == "YEAR_OUT_OF_RANGE" for i in result.issues)

    def test_correct_year_accepted(self):
        result = validate_candidates(
            candidates=[candidate(year=2024)], allowed_years=[2024],
            allowed_evidence_ids={"ev_byd_p127"},
        )
        assert len(result.accepted) == 1


class TestValidateCandidatesUnit:
    def test_illegal_unit_for_metric_rejected(self):
        """单位与指标不符（水量填 m3 给能源）→ 不入库。"""
        cands = [candidate(metric_id="E04", raw_unit="立方米")]
        result = validate_candidates(
            candidates=cands, allowed_years=[2024],
            allowed_evidence_ids={"ev_byd_p127"},
        )
        assert len(result.accepted) == 0
        assert any(i["code"] == "UNIT_MISMATCH" for i in result.issues)


class TestValidateCandidatesEvidenceOwnership:
    def test_foreign_task_evidence_rejects_whole_batch(self):
        """外任务证据 → 整批拒绝（安全归属失败）。"""
        cands = [candidate(evidence_ids=["ev_from_other_task"])]
        result = validate_candidates(
            candidates=cands, allowed_years=[2024],
            allowed_evidence_ids={"ev_byd_p127"},  # 不含 ev_from_other_task
        )
        assert len(result.accepted) == 0
        assert any(i["code"] == "EVIDENCE_FOREIGN" for i in result.issues)
        assert result.batch_rejected is True


class TestValidateCandidatesSpecialStatuses:
    def test_scope3_missing_marker_preserved(self):
        """Scope3 历史缺失标记 '/' → 保留 missing_marker，不推定为 0。"""
        cands = [candidate(metric_id="E03", value=None, raw_value="/",
                           status="missing_marker")]
        result = validate_candidates(
            candidates=cands, allowed_years=[2024],
            allowed_evidence_ids={"ev_byd_p127"},
        )
        assert len(result.accepted) == 1
        assert result.accepted[0]["status"] == "missing_marker"
        assert result.accepted[0]["value"] is None

    def test_conflict_candidates_both_preserved(self):
        """同指标同年两个不同值 → 两条冲突候选都保留，不二选一。"""
        cands = [
            candidate(metric_id="E02", value="0.2049", status="conflict",
                      extraction_note="p42 正文"),
            candidate(metric_id="E02", value="0.2088", status="conflict",
                      extraction_note="p127 附录"),
        ]
        result = validate_candidates(
            candidates=cands, allowed_years=[2024],
            allowed_evidence_ids={"ev_byd_p127"},
        )
        assert len(result.accepted) == 2
        assert {c["value"] for c in result.accepted} == {"0.2049", "0.2088"}

    def test_unknown_has_reason(self):
        """unknown 候选必须有 extraction_note 原因，可追踪。"""
        cands = [candidate(status="unknown", value=None, raw_value=None,
                           extraction_note="当前证据未定位到该组合")]
        result = validate_candidates(
            candidates=cands, allowed_years=[2024],
            allowed_evidence_ids={"ev_byd_p127"},
        )
        assert len(result.accepted) == 1
        assert result.accepted[0]["extraction_note"]

    def test_unknown_without_reason_flagged(self):
        """unknown 无原因 → issue 标记，不静默接受为正常未知。"""
        cands = [candidate(status="unknown", value=None, raw_value=None,
                           extraction_note="")]
        result = validate_candidates(
            candidates=cands, allowed_years=[2024],
            allowed_evidence_ids={"ev_byd_p127"},
        )
        assert any(i["code"] == "UNKNOWN_WITHOUT_REASON" for i in result.issues)


class TestExtractMetricsGuards:
    """主入口的 revision / 幂等 / 人工确认保护。"""

    def _deps(self, model_fn):
        return ExtractionDeps(model_fn=model_fn, extractor_version="test-extractor-v1",
                              model_id="offline-stub", prompt_version="metric-extraction-v0.2.0")

    def test_revision_change_rejects_commit(self):
        """提取过程中 revision 变化 → 拒绝提交。"""
        deps = self._deps(lambda ctx: [candidate()])
        with pytest.raises(Exception) as e:
            extract_metrics(task_id="t1", expected_revision=6,
                            current_revision=7,  # 已变化
                            metric_ids=["E01"], years=[2024],
                            allowed_evidence_ids={"ev_byd_p127"}, deps=deps)
        assert "REVISION" in str(e.value).upper()

    def test_idempotent_key_skips_duplicate_model_call(self):
        """重复幂等 key → 不重复调用模型，返回缓存结果。"""
        calls = []
        def model_fn(ctx):
            calls.append(1)
            return [candidate()]

        deps = self._deps(model_fn)
        cache = {}
        r1 = extract_metrics(task_id="t1", expected_revision=6, current_revision=6,
                             metric_ids=["E01"], years=[2024],
                             allowed_evidence_ids={"ev_byd_p127"}, deps=deps,
                             idempotency_cache=cache, idempotency_key="k1")
        r2 = extract_metrics(task_id="t1", expected_revision=6, current_revision=6,
                             metric_ids=["E01"], years=[2024],
                             allowed_evidence_ids={"ev_byd_p127"}, deps=deps,
                             idempotency_cache=cache, idempotency_key="k1")
        assert len(calls) == 1, "幂等key未阻止重复模型调用"
        assert r2.run.usage["model_calls"] == 0

    def test_reextract_does_not_overwrite_manual_confirmed(self):
        """重提取不得覆盖人工已确认记录。"""
        deps = self._deps(lambda ctx: [candidate(value="999.99")])
        result = extract_metrics(
            task_id="t1", expected_revision=6, current_revision=6,
            metric_ids=["E01"], years=[2024],
            allowed_evidence_ids={"ev_byd_p127"}, deps=deps,
            confirmed_record_keys={("E01", 2024)},
        )
        # 人工确认的 (E01,2024) 对应的模型候选应被排除，不覆盖
        assert all(c["value"] != "999.99" for c in result.records) or \
               any(i["code"] == "MANUAL_CONFIRMED_SKIPPED" for i in result.issues)
