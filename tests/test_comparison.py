"""Task 4: 可比性、跨年计算、对称情景变换测试。

handoff §5 推理错误清单 + Task 4 验收项：
- parse_decimal 千分位/尾随零/占位符不转 0
- compare_records：基期0、缺失、年度颠倒、分母不同、边界未知、重述冲突
  都不输出普通同比；首次自动候选默认 needs_review
- apply_scenario：对称扣绿证两年都扣=+32.52%，只扣一年=not_comparable；
  原值/情景隔离（不覆盖披露值）
"""
import sys
from pathlib import Path
from decimal import Decimal
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.comparison import (
    parse_decimal, compare_records, apply_scenario, ScenarioTransform,
)


def rec(metric_id="E04", year=2023, value="16592592.44", unit="MWh",
        report_id="byd_r1", review_state="confirmed", boundary_key="scope_cn",
        method_text="按报告披露口径核算（已核对）", denominator=None, status="extracted", **kw):
    """构造符合契约的 MetricRecord（服务端字段齐全）。"""
    d = {
        "report_id": report_id, "metric_id": metric_id, "metric_year": year,
        "raw_value": value, "value": value, "raw_unit": unit,
        "canonical_unit": unit, "conversion_rule": None,
        "denominator": denominator, "boundary_text": "", "boundary_key": boundary_key,
        "method_text": method_text, "scope3_categories": [],
        "evidence_ids": [f"ev_{metric_id}_{year}"],
        "status": status, "origin": "reported", "supersedes_id": None,
        "derivation": None, "record_id": f"r_{metric_id}_{year}",
        "task_id": "t1", "review_state": review_state, "revision": 5,
        "is_current": True, "extraction_note": "",
    }
    d.update(kw)
    return d


class TestParseDecimal:
    def test_thousands_separator_and_trailing_zero(self):
        assert parse_decimal("23,552,569.28") == Decimal("23552569.28")
        assert parse_decimal("100.00") == Decimal("100.00")

    def test_placeholder_not_zero(self):
        """'/' '—' 未披露 不转 0。"""
        assert parse_decimal("/") is None
        assert parse_decimal("—") is None
        assert parse_decimal("未披露") is None
        assert parse_decimal("") is None

    def test_explicit_zero_is_zero(self):
        assert parse_decimal("0") == Decimal("0")
        assert parse_decimal("0.00") == Decimal("0.00")


class TestCompareRecords:
    def test_normal_yoy_e04(self):
        """正常同比：E04 16,592,592.44 → 23,552,569.28 = +41.95%。"""
        older = rec(year=2023, value="16592592.44")
        newer = rec(year=2024, value="23552569.28")
        r = compare_records(older, newer)
        assert r["status"] == "comparable"
        assert r["percent_change"] == "41.95"
        assert r["delta"] == "6959976.84"

    def test_normal_yoy_e01_negative(self):
        """E01 下降：12,341,455 → 10,101,826.20 = -18.15%。"""
        older = rec(metric_id="E01", year=2023, value="12341455.00", unit="tCO2e")
        newer = rec(metric_id="E01", year=2024, value="10101826.20", unit="tCO2e")
        r = compare_records(older, newer)
        assert r["status"] == "comparable"
        assert r["percent_change"] == "-18.15"

    def test_base_zero_undefined(self):
        """上年=0 → undefined，不算百分比。"""
        older = rec(year=2023, value="0")
        newer = rec(year=2024, value="100")
        r = compare_records(older, newer)
        assert r["status"] == "undefined"
        assert r["percent_change"] is None
        assert "BASE_ZERO" in r["reason_codes"]

    def test_missing_value_not_comparable(self):
        """数值缺失 → not_comparable。"""
        older = rec(year=2023, value="/", status="missing_marker")
        newer = rec(year=2024, value="100")
        r = compare_records(older, newer)
        assert r["status"] == "not_comparable"
        assert r["percent_change"] is None
        assert "MISSING_VALUE" in r["reason_codes"]

    def test_reversed_years_not_comparable(self):
        """年度颠倒（older 反而是大年）→ not_comparable。"""
        older = rec(year=2024, value="100")
        newer = rec(year=2023, value="90")
        r = compare_records(older, newer)
        assert r["status"] == "not_comparable"
        assert "DEFINITION_MISMATCH" in r["reason_codes"]

    def test_different_unit_not_comparable(self):
        """单位不一致 → not_comparable。"""
        older = rec(year=2023, value="100", unit="MWh")
        newer = rec(year=2024, value="90", unit="吨标准煤")
        r = compare_records(older, newer)
        assert r["status"] == "not_comparable"
        assert "UNIT_MISMATCH" in r["reason_codes"]

    def test_different_denominator_not_comparable(self):
        """强度分母不同 → not_comparable（营收 vs 每辆车）。"""
        d1 = {"kind": "万元营收", "currency": "CNY", "scale": "1", "unit": "万元营收"}
        d2 = {"kind": "每辆车", "currency": None, "scale": "1", "unit": "辆"}
        older = rec(year=2023, value="0.2", denominator=d1)
        newer = rec(year=2024, value="0.18", denominator=d2)
        r = compare_records(older, newer)
        assert r["status"] == "not_comparable"
        assert "DENOMINATOR_MISMATCH" in r["reason_codes"]

    def test_unconfirmed_boundary_needs_review(self):
        """边界未核对（纯字符串相同不算一致）→ needs_review，不输出同比。"""
        older = rec(year=2023, boundary_key=None, review_state="confirmed")
        newer = rec(year=2024, boundary_key=None, review_state="confirmed")
        r = compare_records(older, newer)
        assert r["status"] == "needs_review"
        assert r["percent_change"] is None
        assert "BOUNDARY_UNCONFIRMED" in r["reason_codes"]

    def test_unreviewed_default_needs_review(self):
        """首次自动候选（unreviewed）默认 needs_review，需人工确认后才算。"""
        older = rec(year=2023, review_state="unreviewed")
        newer = rec(year=2024, review_state="unreviewed")
        r = compare_records(older, newer)
        assert r["status"] == "needs_review"
        assert r["percent_change"] is None

    def test_conflicting_values_not_comparable(self):
        """新旧资料冲突（status=conflict）→ 保留候选，not_comparable。"""
        older = rec(year=2023, value="100", status="conflict")
        newer = rec(year=2024, value="90")
        r = compare_records(older, newer)
        assert r["status"] == "not_comparable"
        assert "CONFLICTING_VALUES" in r["reason_codes"]


class TestSymmetricScenario:
    def test_both_years_adjustment_yields_32_52(self):
        """对称扣绿证：两年都扣 → +32.52%（handoff 用例）。"""
        older = rec(year=2023, value="16592592.44")
        newer = rec(year=2024, value="23552569.28")
        transform = ScenarioTransform(
            formula_id="WITHHOLD_CERTIFICATE",
            params_by_year={2023: {"certificate": "508000"},
                            2024: {"certificate": "2237986"}},
            assumption="剔除绿证（仅示例情景，非唯一核算方法）",
        )
        r = apply_scenario([older, newer], transform)
        assert r["status"] == "ok"
        adj = {x["metric_year"]: x["scenario_value"] for x in r["records"]}
        assert adj[2023] == "16084592.44"
        assert adj[2024] == "21314583.28"
        # 同比
        cmp = r["comparison"]
        assert cmp["status"] == "comparable"
        assert cmp["percent_change"] == "32.52"

    def test_one_year_only_rejected(self):
        """只扣 2024 一年 → 不一致变换，not_comparable（不得得 -0.44%）。"""
        older = rec(year=2023, value="16592592.44")
        newer = rec(year=2024, value="23552569.28")
        transform = ScenarioTransform(
            formula_id="WITHHOLD_CERTIFICATE",
            params_by_year={2024: {"certificate": "2237986"}},  # 缺 2023
            assumption="只扣一年",
        )
        r = apply_scenario([older, newer], transform)
        assert r["status"] == "rejected"
        assert "ASYMMETRIC_TRANSFORM" in r["reason_codes"]

    def test_original_values_not_overwritten(self):
        """情景值不覆盖原披露值：原始 value 保持不变。"""
        older = rec(year=2023, value="16592592.44")
        newer = rec(year=2024, value="23552569.28")
        transform = ScenarioTransform(
            formula_id="WITHHOLD_CERTIFICATE",
            params_by_year={2023: {"certificate": "508000"},
                            2024: {"certificate": "2237986"}},
            assumption="示例情景",
        )
        r = apply_scenario([older, newer], transform)
        for sr in r["records"]:
            # scenario_value 与原始 value 分离
            assert sr["scenario_value"] != sr["value"]
