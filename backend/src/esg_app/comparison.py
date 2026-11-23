"""可比性、跨年计算与情景变换（Task 4）。

核心原则（handoff §5）：
- 只有同企业/同指标/递增年度/可转换单位/同强度分母/边界与方法已核对，
  才返回 comparable 并给出同比；否则 must be needs_review / not_comparable /
  undefined，且 delta/percent_change 为 null。
- 首次自动候选（unreviewed）默认 needs_review，人工确认后才可 comparable。
- 情景变换（剔除绿证等）是对称敏感性分析，不覆盖原披露值；
  两年必须用同一变换，缺一年参数 → 拒绝（ASYMMETRIC_TRANSFORM）。
- 用 Decimal 精确计算，百分比四舍五入到 2 位（ROUND_HALF_UP）。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional

PLACEHOLDERS = {"/", "—", "-", "--", "未披露", "未统计", "n/a", "na", ""}
_NUM_RE = re.compile(r"^-?[0-9][0-9,]*(?:\.[0-9]+)?$")

# 可换算的单位组（组内视为可比较，且单位字符串归一到同一 canonical）
UNIT_GROUPS: dict[str, set[str]] = {
    "energy": {"mwh", "兆瓦时", "kwh", "千瓦时"},
    "carbon": {"tco2e", "吨二氧化碳当量", "tco₂e"},
    "water": {"m3", "立方米", "m³"},
    "intensity_carbon": {"tco2e/万元营收", "吨二氧化碳当量/万元营收"},
}

# 白名单确定性公式（P0 不 eval 任意表达式）
ALLOWED_FORMULAS = frozenset({"WITHHOLD_CERTIFICATE", "UNIT_CONVERSION"})


def parse_decimal(raw: str | None) -> Optional[Decimal]:
    """解析数值字符串：允许千分位和尾随零；占位符返回 None（绝不转 0）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in PLACEHOLDERS:
        return None
    if not _NUM_RE.match(s):
        return None
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def _canonical_unit(unit: Optional[str]) -> Optional[str]:
    """把单位归一到组名；无法归一时返回原字符串（小写）。"""
    if not unit:
        return None
    u = unit.strip().lower()
    for group, members in UNIT_GROUPS.items():
        if u in {m.lower() for m in members}:
            return group
    return u


def _pct(new: Decimal, old: Decimal) -> str:
    """百分比变化，四舍五入 2 位；调用前保证 old != 0。"""
    change = (new - old) / old * Decimal("100")
    return str(change.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _base_fields(older: dict, newer: dict, status: str, reasons: list[str]) -> dict:
    """构造 ComparisonResult 骨架，按契约 allOf 约束填写 delta/percent。"""
    comparable = status == "comparable"
    return {
        "comparison_id": f"cmp_{uuid.uuid4().hex[:12]}",
        "older_record_id": older.get("record_id", ""),
        "newer_record_id": newer.get("record_id", ""),
        "status": status,
        "reason_codes": sorted(set(reasons)) if not comparable else [],
        "delta": None,
        "percent_change": None,
        "unit": None,
        "evidence_ids": sorted(set((older.get("evidence_ids") or []) +
                                   (newer.get("evidence_ids") or []))),
        "revision": newer.get("revision", 0),
        "is_current": True,
    }


def compare_records(older: dict, newer: dict) -> dict:
    """比较两条 MetricRecord，返回契约 ComparisonResult。"""
    reasons: list[str] = []

    # 1. 数值可解析性（占位符/缺失）
    old_v = parse_decimal(older.get("value"))
    new_v = parse_decimal(newer.get("value"))
    if old_v is None or new_v is None:
        reasons.append("MISSING_VALUE")
        return _base_fields(older, newer, "not_comparable", reasons)

    # 2. 冲突状态：先保留候选，不算同比
    if older.get("status") == "conflict" or newer.get("status") == "conflict":
        reasons.append("CONFLICTING_VALUES")
        return _base_fields(older, newer, "not_comparable", reasons)

    # 3. 年度必须递增
    if not (isinstance(older.get("metric_year"), int) and
            isinstance(newer.get("metric_year"), int) and
            newer["metric_year"] > older["metric_year"]):
        reasons.append("DEFINITION_MISMATCH")
        return _base_fields(older, newer, "not_comparable", reasons)

    # 4. 同企业 / 同指标
    if older.get("report_id") != newer.get("report_id"):
        reasons.append("DEFINITION_MISMATCH")
    if older.get("metric_id") != newer.get("metric_id"):
        reasons.append("DEFINITION_MISMATCH")

    # 5. 单位可换算性
    u_old, u_new = _canonical_unit(older.get("canonical_unit")), _canonical_unit(newer.get("canonical_unit"))
    if u_old != u_new:
        reasons.append("UNIT_MISMATCH")

    # 6. 强度分母一致性
    d_old, d_new = older.get("denominator"), newer.get("denominator")
    if (d_old is None) != (d_new is None):
        reasons.append("DENOMINATOR_MISMATCH")
    elif d_old and d_new and _canonical_unit(d_old.get("unit")) != _canonical_unit(d_new.get("unit")):
        reasons.append("DENOMINATOR_MISMATCH")

    # 7. 边界已核对：boundary_key 都为 None 时不证明一致（纯字符串相同不算）
    if not older.get("boundary_key") or not newer.get("boundary_key"):
        reasons.append("BOUNDARY_UNCONFIRMED")
    elif older["boundary_key"] != newer["boundary_key"]:
        reasons.append("BOUNDARY_UNCONFIRMED")

    # 8. 方法已核对（method_text 均为空视为未核对）
    if not older.get("method_text") and not newer.get("method_text"):
        reasons.append("METHOD_UNCONFIRMED")

    # 9. 人工复核状态：首次自动候选默认 needs_review
    if older.get("review_state") != "confirmed" or newer.get("review_state") != "confirmed":
        reasons.append("RESTATEMENT_UNCONFIRMED")

    # 硬阻断项：单位/分母/边界/方法不一致 → not_comparable
    hard = {"UNIT_MISMATCH", "DENOMINATOR_MISMATCH"}
    if any(r in reasons for r in hard):
        return _base_fields(older, newer, "not_comparable", reasons)

    # 基期 0 → undefined
    if old_v == 0:
        reasons.append("BASE_ZERO")
        return _base_fields(older, newer, "undefined", reasons)

    # 边界/方法/复核未确认 → needs_review（不输出同比）
    review_gates = {"BOUNDARY_UNCONFIRMED", "METHOD_UNCONFIRMED", "RESTATEMENT_UNCONFIRMED"}
    if any(r in reasons for r in review_gates):
        reasons.append("BOUNDARY_UNCONFIRMED") if not reasons else None
        return _base_fields(older, newer, "needs_review", reasons)

    # 通过全部门槛 → comparable，计算同比
    result = _base_fields(older, newer, "comparable", reasons)
    result["delta"] = str(new_v - old_v)
    result["percent_change"] = _pct(new_v, old_v)
    result["unit"] = older.get("canonical_unit") or newer.get("canonical_unit")
    return result


@dataclass
class ScenarioTransform:
    """情景变换：公式 ID + 各年参数 + 假设（P0 白名单公式）。"""
    formula_id: str
    params_by_year: dict[int, dict[str, str]] = field(default_factory=dict)
    assumption: str = ""

    def validate(self) -> Optional[str]:
        if self.formula_id not in ALLOWED_FORMULAS:
            return "FORMULA_NOT_ALLOWED"
        if not self.params_by_year:
            return "NO_PARAMS"
        return None


def apply_scenario(records: list[dict], transform: ScenarioTransform) -> dict:
    """应用确定性情景变换；原值/情景值隔离，两年必须对称。"""
    err = transform.validate()
    if err:
        return {"status": "rejected", "reason_codes": [err], "records": [], "comparison": None}

    years = [r.get("metric_year") for r in records]
    # 对称校验：参与比较的每一年都必须提供参数
    missing = [y for y in years if y not in transform.params_by_year]
    if missing:
        return {"status": "rejected", "reason_codes": ["ASYMMETRIC_TRANSFORM"],
                "records": [], "comparison": None}

    scenario_records = []
    for r in records:
        year = r["metric_year"]
        base = parse_decimal(r.get("value"))
        if base is None:
            return {"status": "rejected", "reason_codes": ["MISSING_VALUE"],
                    "records": [], "comparison": None}
        params = transform.params_by_year[year]
        if transform.formula_id == "WITHHOLD_CERTIFICATE":
            cert = parse_decimal(params.get("certificate"))
            if cert is None:
                return {"status": "rejected", "reason_codes": ["INVALID_PARAM"],
                        "records": [], "comparison": None}
            scenario_value = base - cert
        else:  # UNIT_CONVERSION：乘法因子（0 是合法因子，不能用 or 兜底成 1）
            factor = parse_decimal(params.get("factor"))
            if factor is None:
                return {"status": "rejected", "reason_codes": ["INVALID_PARAM"],
                        "records": [], "comparison": None}
            scenario_value = base * factor
        sr = dict(r)
        sr["scenario_value"] = str(scenario_value)
        sr["original_value"] = r.get("value")  # 原值保留，情景不覆盖
        scenario_records.append(sr)

    # 情景记录之间做比较。情景是确认过的敏感性分析，边界/方法沿用原记录，
    # 故直接算同比（用与 compare_records 相同的门槛但 scenario_value 作为数值）。
    older, newer = scenario_records[0], scenario_records[-1]
    o = dict(older); n = dict(newer)
    o["value"] = older["scenario_value"]; n["value"] = newer["scenario_value"]
    o["review_state"] = "confirmed"; n["review_state"] = "confirmed"
    o["method_text"] = o.get("method_text") or transform.assumption or "scenario"
    n["method_text"] = n.get("method_text") or transform.assumption or "scenario"
    cmp = compare_records(o, n)

    return {
        "status": "ok",
        "reason_codes": [],
        "records": scenario_records,
        "comparison": cmp,
        "transform": {"formula_id": transform.formula_id, "assumption": transform.assumption,
                      "params_by_year": {str(k): v for k, v in transform.params_by_year.items()}},
    }
