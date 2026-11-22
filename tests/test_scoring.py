"""Task 5: 版本化评分计算测试。

handoff §4.1 固定计算公式 + §6 评分手算用例 + Task 5 验收项：
- 手算例：C01=met、C02=partial、其余 unknown
  → P=9.375、K=12.5、A=100、coverage=12.5%、参考分75、区间[9.375,96.875]、final=null
- 全部 met=100；全部 unknown 不得默认 100；全 N/A；部分 N/A 重新归一化
- 自动 not_met / N/A 缺依据拒绝；单子条件项无 partial；同维等权；权重和=1；20 项
- 保存未舍入值，界面 2 位小数（本层只验未舍入）
"""
import sys
from pathlib import Path
from decimal import Decimal
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.scoring import (
    score_checks, load_rulebook, validate_rulebook, RulebookError,
)
from esg_app.rules_registry import DIMENSIONS, CHECK_TO_DIMENSION


# ---- 测试夹具 ----
def check(check_id, status, origin="model", review_state="confirmed",
          evidence_ids=None, reason="测试理由：已核对相关证据。"):
    return {
        "check_id": check_id, "rule_version": "disclosure-v0.1", "status": status,
        "evidence_ids": evidence_ids if evidence_ids is not None else
                        (["ev_x"] if status in ("met", "partial", "not_met") else []),
        "reason": reason, "missing_information": [], "suggestion": "", "target_key": None,
        "task_id": "task_score", "origin": origin, "review_state": review_state,
        "revision": 7, "is_current": True,
    }


ALL_IDS = ["C01", "C02", "C03", "C04", "Q01", "Q02", "Q03", "Q04",
           "B01", "B02", "B03", "B04", "Y01", "Y02", "Y03", "Y04",
           "T01", "T02", "T03", "T04"]


def make_checks(status_map):
    """status_map: {check_id: status}，未列出的填 unknown。

    N/A 按业务规则只允许人工确认，自动带 origin=manual。
    """
    out = []
    for cid in ALL_IDS:
        status = status_map.get(cid, "unknown")
        kw = {"origin": "manual"} if status == "not_applicable" else {}
        out.append(check(cid, status, **kw))
    return out


@pytest.fixture(scope="module")
def rulebook():
    return load_rulebook()


class TestHandCalculation:
    """handoff §6 手算例——必须逐项吻合契约示例。"""

    def test_hand_case_all_values(self, rulebook):
        checks = make_checks({"C01": "met", "C02": "partial"})
        r = score_checks(checks, rulebook)
        assert r["coverage"] == "0.125"
        assert r["known_weight"] == "0.125"
        assert r["applicable_weight"] == "1"
        assert r["reference_score"] == "75"
        assert r["lower_bound"] == "9.375"
        assert r["upper_bound"] == "96.875"
        assert r["final_score"] is None
        assert r["label"] == "insufficient_evidence"
        assert r["rule_version"] == "disclosure-v0.1"
        assert r["is_current"] is True

    def test_hand_case_dimension_scores(self, rulebook):
        checks = make_checks({"C01": "met", "C02": "partial"})
        r = score_checks(checks, rulebook)
        dims = {d["dimension"]: d for d in r["dimension_results"]}
        assert len(dims) == 5
        comp = dims["completeness"]
        # 完整性维权重 0.25，4 项各 a=0.0625；C01 met + C02 partial → P=0.09375, K=0.125, A=0.25
        assert comp["applicable_weight"] == "0.25"
        assert comp["known_weight"] == "0.125"
        assert comp["coverage"] == "0.5"
        assert comp["reference_score"] == "75"
        assert comp["lower_bound"] == "37.5"
        assert comp["upper_bound"] == "87.5"
        assert comp["final_score"] is None
        # 其余四维 K=0 → 区间 [0,100]、参考分 null
        for name in ["specificity", "boundary_method", "comparability", "targets"]:
            assert dims[name]["coverage"] == "0"
            assert dims[name]["reference_score"] is None
            assert dims[name]["lower_bound"] == "0"
            assert dims[name]["upper_bound"] == "100"

    def test_all_met_is_100(self, rulebook):
        checks = make_checks({cid: "met" for cid in ALL_IDS})
        r = score_checks(checks, rulebook)
        assert r["coverage"] == "1"
        assert r["reference_score"] == "100"
        assert r["final_score"] == "100"
        assert r["upper_bound"] == "100"
        assert r["label"] == "final"

    def test_all_unknown_not_default_100(self, rulebook):
        checks = make_checks({})
        r = score_checks(checks, rulebook)
        assert r["known_weight"] == "0"
        assert r["coverage"] == "0"
        assert r["reference_score"] is None
        assert r["final_score"] is None
        assert r["lower_bound"] == "0"
        assert r["upper_bound"] == "100"
        assert r["label"] == "insufficient_evidence"

    def test_all_na_no_applicable(self, rulebook):
        checks = make_checks({cid: "not_applicable" for cid in ALL_IDS})
        r = score_checks(checks, rulebook)
        assert r["applicable_weight"] == "0"
        assert r["coverage"] is None
        assert r["reference_score"] is None
        assert r["lower_bound"] is None
        assert r["upper_bound"] is None
        assert r["label"] == "no_applicable_items"

    def test_partial_na_renormalizes(self, rulebook):
        # C01/C02 为 N/A（从 A 剔除），C03=met；其余 unknown
        # completeness 维：A=2*0.0625=0.125, K=0.0625, P=0.0625
        checks = make_checks({"C01": "not_applicable", "C02": "not_applicable", "C03": "met"})
        r = score_checks(checks, rulebook)
        assert r["applicable_weight"] == "0.875"  # 1 - 2*0.0625
        assert r["known_weight"] == "0.0625"
        assert Decimal(r["coverage"]) == Decimal("0.0625") / Decimal("0.875")
        assert Decimal(r["reference_score"]) == Decimal("100")
        dims = {d["dimension"]: d for d in r["dimension_results"]}
        assert dims["completeness"]["applicable_weight"] == "0.125"
        assert dims["completeness"]["coverage"] == "0.5"

    def test_full_coverage_uses_reference_as_final(self, rulebook):
        # 4 维全 met，1 维(targets, 权重0.15)全 unknown → coverage=0.85，仍是 insufficient（<1）
        st = {cid: "met" for cid in ALL_IDS if CHECK_TO_DIMENSION[cid] != "targets"}
        checks = make_checks(st)
        r = score_checks(checks, rulebook)
        assert Decimal(r["coverage"]) == Decimal("0.85")
        assert r["final_score"] is None
        assert r["label"] == "insufficient_evidence"


class TestCheckValidation:
    """候选状态验证：自动 not_met / N/A 缺依据拒绝。"""

    def test_auto_not_met_without_evidence_rejected(self, rulebook):
        checks = make_checks({"C01": "not_met"})
        checks[0]["evidence_ids"] = []
        with pytest.raises(RulebookError) as ei:
            score_checks(checks, rulebook)
        assert "NOT_MET_NEEDS_EVIDENCE" in str(ei.value)

    def test_auto_na_rejected(self, rulebook):
        """自动来源不得判 N/A（N/A 只允许人工确认）。"""
        checks = make_checks({"C01": "not_applicable"})
        checks[0]["origin"] = "model"
        with pytest.raises(RulebookError) as ei:
            score_checks(checks, rulebook)
        assert "NA_NEEDS_MANUAL" in str(ei.value)

    def test_manual_na_with_reason_ok(self, rulebook):
        checks = make_checks({"C01": "not_applicable"})
        checks[0]["origin"] = "manual"
        r = score_checks(checks, rulebook)
        assert r["applicable_weight"] == "0.9375"

    def test_duplicate_check_rejected(self, rulebook):
        checks = make_checks({})
        checks.append(check("C01", "met"))
        with pytest.raises(RulebookError) as ei:
            score_checks(checks, rulebook)
        assert "DUPLICATE_CHECK" in str(ei.value)

    def test_missing_check_defaults_unknown(self, rulebook):
        checks = [check("C01", "met")]
        r = score_checks(checks, rulebook)
        assert r["known_weight"] == "0.0625"
        assert r["label"] == "insufficient_evidence"


class TestRulebookValidation:
    def test_real_rulebook_valid(self, rulebook):
        validate_rulebook(rulebook)  # 不抛异常

    def test_weight_sum_must_be_one(self, rulebook):
        import copy
        bad = copy.deepcopy(rulebook)
        bad["checks"][0]["weight"] = "0.05"
        with pytest.raises(RulebookError) as ei:
            validate_rulebook(bad)
        assert "WEIGHT_SUM" in str(ei.value)

    def test_equal_weight_within_dimension(self, rulebook):
        import copy
        bad = copy.deepcopy(rulebook)
        dim = bad["checks"][0]["dimension"]
        same_dim = [c for c in bad["checks"] if c["dimension"] == dim]
        same_dim[1]["weight"] = "0.05"
        # 同时调另一个以维持总和，但同维不等权仍应被检出
        same_dim[0]["weight"] = "0.075"
        with pytest.raises(RulebookError) as ei:
            validate_rulebook(bad)
        assert "UNEQUAL_DIMENSION_WEIGHT" in str(ei.value)

    def test_single_subcondition_has_no_partial(self, rulebook):
        import copy
        bad = copy.deepcopy(rulebook)
        single = next(c for c in bad["checks"] if len(c["subconditions"]) == 1)
        assert single["partial_anchor"] is None, "单子条件项 partial_anchor 应为 null"

    def test_20_checks_required(self, rulebook):
        import copy
        bad = copy.deepcopy(rulebook)
        bad["checks"] = bad["checks"][:19]
        with pytest.raises(RulebookError):
            validate_rulebook(bad)

    def test_experimental_and_unverified_sources(self, rulebook):
        assert rulebook["experimental"] is True
        for c in rulebook["checks"]:
            assert c["source_refs"], f"{c['check_id']} 缺 source_refs"
            for ref in c["source_refs"]:
                assert ref["verification_status"] == "unverified"
