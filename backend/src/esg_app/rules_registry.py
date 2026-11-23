"""评分规则注册表常量（Task 5）。

维度权重与检查项归属对齐 rules/disclosure-v0.1.json 与契约 DimensionScore。
指标→依赖检查的映射用于复核/指标变更后使关联检查失效（handoff Task 5）。
"""
from __future__ import annotations

# 五维权重（契约 weight 用 0-1），每项 = 维度权重/4
DIMENSIONS: dict[str, dict] = {
    "completeness":    {"weight": "0.25", "label": "完整性"},
    "specificity":     {"weight": "0.20", "label": "具体性"},
    "boundary_method": {"weight": "0.20", "label": "边界方法"},
    "comparability":   {"weight": "0.20", "label": "跨年可比"},
    "targets":         {"weight": "0.15", "label": "目标追踪"},
}
DIMENSION_ORDER = ["completeness", "specificity", "boundary_method", "comparability", "targets"]

CHECK_TO_DIMENSION: dict[str, str] = {
    "C01": "completeness", "C02": "completeness", "C03": "completeness", "C04": "completeness",
    "Q01": "specificity", "Q02": "specificity", "Q03": "specificity", "Q04": "specificity",
    "B01": "boundary_method", "B02": "boundary_method", "B03": "boundary_method", "B04": "boundary_method",
    "Y01": "comparability", "Y02": "comparability", "Y03": "comparability", "Y04": "comparability",
    "T01": "targets", "T02": "targets", "T03": "targets", "T04": "targets",
}
ALL_CHECK_IDS = tuple(CHECK_TO_DIMENSION.keys())

# 六个核心指标 → 依赖其数值的检查项。
# 依据 handoff §4 子条件：C01(E01,E02)、C03(E04)、C04(E05,E06)、Q01/Q02(全部数值记录)、
# Y01(E01,E02,E04,E05,E06)。指标值变更后这些检查必须失效为 unknown/needs_review。
METRIC_TO_CHECKS: dict[str, frozenset[str]] = {
    "E01": frozenset({"C01", "Q01", "Q02", "Y01"}),
    "E02": frozenset({"C01", "Q01", "Q02", "Q03", "Y01"}),
    "E03": frozenset({"C02", "Q01", "Q02"}),
    "E04": frozenset({"C03", "Q01", "Q02", "Y01"}),
    "E05": frozenset({"C04", "Q01", "Q02", "Y01"}),
    "E06": frozenset({"C04", "Q01", "Q02", "Y01"}),
}
