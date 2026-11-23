"""版本化披露质量评分（Task 5）。

严格实现 handoff §4.1 固定计算公式，保存未舍入值（Decimal）：
- a = 维度权重/4；met=1、partial=0.5、not_met=0
- A = 适用权重（剔除人工确认 N/A）；K = 已知权重（met/partial/not_met）
- P = 加权已知分
- coverage=K/A；reference=100P/K；lower=100P/A；upper=100(P+A-K)/A
- coverage=1 → final=reference，否则 final=null
- A=0 → 全 null，label=no_applicable_items；K=0 且 A>0 → coverage=0、区间[0,100]、参考分 null
- 各维用同样逻辑，禁止未知默认 0 后平均
"""
from __future__ import annotations

import json
from decimal import Decimal, getcontext
from pathlib import Path

from .rules_registry import (
    DIMENSIONS, DIMENSION_ORDER, CHECK_TO_DIMENSION, ALL_CHECK_IDS,
)

getcontext().prec = 28

_RULE_PATH = Path(__file__).resolve().parent / "rules" / "disclosure-v0.1.json"
_STATUS_VALUE = {"met": Decimal("1"), "partial": Decimal("0.5"), "not_met": Decimal("0")}
_KNOWN = frozenset({"met", "partial", "not_met"})


class RulebookError(ValueError):
    """规则或检查候选不合法（携带错误码便于 issue 映射）。"""


def _dec_str(d: Decimal) -> str:
    """Decimal → 契约字符串：去尾部 0 但保留整数（100 不被截成 1）。"""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def load_rulebook(path: Path | None = None) -> dict:
    p = path or _RULE_PATH
    rb = json.loads(p.read_text(encoding="utf-8"))
    validate_rulebook(rb)
    return rb


def validate_rulebook(rulebook: dict) -> None:
    if rulebook.get("experimental") is not True:
        raise RulebookError("NOT_EXPERIMENTAL")
    checks = rulebook.get("checks", [])
    if len(checks) != 20:
        raise RulebookError("CHECK_COUNT")
    ids = [c["check_id"] for c in checks]
    if set(ids) != set(ALL_CHECK_IDS) or len(ids) != len(set(ids)):
        raise RulebookError("CHECK_IDS")
    # 权重和 = 1
    total = sum((Decimal(c["weight"]) for c in checks), Decimal("0"))
    if total != Decimal("1"):
        raise RulebookError("WEIGHT_SUM")
    # 同维等权 + 单子条件无 partial
    by_dim: dict[str, list[Decimal]] = {}
    for c in checks:
        by_dim.setdefault(c["dimension"], []).append(Decimal(c["weight"]))
        if len(c["subconditions"]) == 1 and c.get("partial_anchor") is not None:
            raise RulebookError("SINGLE_SUBCONDITION_HAS_PARTIAL")
    for dim, weights in by_dim.items():
        if len(set(weights)) != 1:
            raise RulebookError("UNEQUAL_DIMENSION_WEIGHT")
        if len(weights) != 4:
            raise RulebookError("DIMENSION_ITEM_COUNT")
        expected = Decimal(DIMENSIONS[dim]["weight"]) / 4
        if weights[0] != expected:
            raise RulebookError("WEIGHT_MISMATCH")


def _score_items(items: list[tuple[Decimal, str]]) -> dict:
    """给定 [(weight, status)] 计算一组的分数（未舍入）。"""
    A = sum((w for w, s in items if s != "not_applicable"), Decimal("0"))
    K = sum((w for w, s in items if s in _KNOWN), Decimal("0"))
    P = sum((w * _STATUS_VALUE[s] for w, s in items if s in _KNOWN), Decimal("0"))

    if A == 0:
        return {"applicable_weight": "0", "known_weight": "0", "coverage": None,
                "reference_score": None, "final_score": None,
                "lower_bound": None, "upper_bound": None}

    coverage = K / A
    lower = 100 * P / A
    upper = 100 * (P + A - K) / A
    if K > 0:
        reference = 100 * P / K
        final = reference if coverage == 1 else None
    else:
        reference = None
        final = None

    return {
        "applicable_weight": _dec_str(A),
        "known_weight": _dec_str(K),
        "coverage": _dec_str(coverage),
        "reference_score": _dec_str(reference) if reference is not None else None,
        "final_score": _dec_str(final) if final is not None else None,
        "lower_bound": _dec_str(lower),
        "upper_bound": _dec_str(upper),
    }


def score_checks(checks: list[dict], rulebook: dict) -> dict:
    validate_rulebook(rulebook)
    weight_of = {c["check_id"]: Decimal(c["weight"]) for c in rulebook["checks"]}

    # 候选状态校验
    seen: set[str] = set()
    status_by_id: dict[str, str] = {}
    for c in checks:
        cid = c.get("check_id")
        status = c.get("status")
        if cid in seen:
            raise RulebookError("DUPLICATE_CHECK")
        seen.add(cid)
        if cid not in weight_of:
            raise RulebookError("UNKNOWN_CHECK_ID")
        if status not in _KNOWN | {"unknown", "not_applicable"}:
            raise RulebookError("BAD_STATUS")
        if status == "not_met" and not c.get("evidence_ids"):
            raise RulebookError("NOT_MET_NEEDS_EVIDENCE")
        if status == "not_applicable" and c.get("origin") != "manual":
            raise RulebookError("NA_NEEDS_MANUAL")
        status_by_id[cid] = status

    # 缺失项默认 unknown；仅 is_current=True 的项计入（历史版本不参与当前评分）
    current = [c for c in checks if c.get("is_current", True)]
    by_id = {c["check_id"]: c for c in current}
    effective = {cid: by_id[cid]["status"] if cid in by_id else "unknown" for cid in ALL_CHECK_IDS}

    # 全局
    all_items = [(weight_of[cid], effective[cid]) for cid in ALL_CHECK_IDS]
    result = _score_items(all_items)

    # 各维
    dim_results = []
    for dim in DIMENSION_ORDER:
        dim_ids = [cid for cid in ALL_CHECK_IDS if CHECK_TO_DIMENSION[cid] == dim]
        d = _score_items([(weight_of[cid], effective[cid]) for cid in dim_ids])
        # 无 N/A 剔除时，维度适用权重采用规则定义的原始字符串格式（0.20 而非 0.2），
        # 与契约示例展示一致；有 N/A 时保留计算值（可能非整）。
        if not any(effective[cid] == "not_applicable" for cid in dim_ids):
            d["applicable_weight"] = DIMENSIONS[dim]["weight"]
        dim_results.append({"dimension": dim, **d})

    coverage = result["coverage"]
    if result["applicable_weight"] == "0":
        label = "no_applicable_items"
    elif coverage == "1":
        label = "final"
    else:
        label = "insufficient_evidence"

    revision = checks[0].get("revision", 0) if checks else 0
    return {
        "revision": revision,
        "rule_version": rulebook["rule_version"],
        "coverage": coverage,
        "reference_score": result["reference_score"],
        "final_score": result["final_score"],
        "lower_bound": result["lower_bound"],
        "upper_bound": result["upper_bound"],
        "applicable_weight": result["applicable_weight"],
        "known_weight": result["known_weight"],
        "dimension_results": dim_results,
        "label": label,
        "is_current": True,
    }
