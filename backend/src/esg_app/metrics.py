"""内部候选校验（Task 3）。

validate_candidates 是主 Agent 不可调用的内部函数：它把模型产出的
MetricCandidate 逐条校验，只有符合任务范围/单位口径/证据归属的候选
才允许进入入库流程。失败不静默丢弃，全部记录为 issue。

核心安全原则（handoff Task 3 / 提示词 §2,§3,§5）：
- 年份越界 → 拒绝
- 单位与指标口径不符 → 拒绝
- 引用了任务外证据 → 整批拒绝（安全归属失败）
- missing_marker / unknown / conflict 等合法状态保留，不推定为 0
- unknown 必须有原因，否则标记 issue
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .schemas import VALID_METRIC_IDS, UNIT_MARKERS, make_issue


@dataclass
class CandidateValidation:
    accepted: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    batch_rejected: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.accepted) and not self.batch_rejected


def _unit_ok(metric_id: str, raw_unit: Optional[str]) -> bool:
    """检查原始单位是否属于该指标的合法单位集合。"""
    if not raw_unit:
        # 无单位：missing_marker/unknown 允许，extracted 不允许（另行校验）
        return True
    u = raw_unit.lower()
    return any(m in u for m in UNIT_MARKERS.get(metric_id, []))


def validate_candidates(
    candidates: list[dict],
    allowed_years: list[int],
    allowed_evidence_ids: set[str],
    allowed_metric_ids: Optional[set[str]] = None,
) -> CandidateValidation:
    """逐条校验候选；返回被接受候选 + 全部问题码。

    allowed_years / allowed_metric_ids 以服务端任务为准，不信任模型传入。
    """
    allowed_metrics = allowed_metric_ids or set(VALID_METRIC_IDS)
    allowed_years_set = set(allowed_years)
    result = CandidateValidation()

    # 证据归属预检：任一条候选引用了任务外证据 → 整批拒绝
    for c in candidates:
        evidence_ids = c.get("evidence_ids") or []
        foreign = [eid for eid in evidence_ids if eid not in allowed_evidence_ids]
        if foreign:
            result.batch_rejected = True
            result.issues.append(make_issue(
                "EVIDENCE_FOREIGN",
                f"candidate references evidence outside task scope: {foreign}",
                report_id=c.get("report_id"),
            ))

    if result.batch_rejected:
        # 安全归属失败：整批不入库，也不再逐条接受
        return result

    for c in candidates:
        metric_id = c.get("metric_id")
        year = c.get("metric_year")
        status = c.get("status")
        note = c.get("extraction_note") or ""

        # 年份越界
        if year not in allowed_years_set:
            result.issues.append(make_issue(
                "YEAR_OUT_OF_RANGE",
                f"metric_year {year} not in task years {sorted(allowed_years_set)}",
                report_id=c.get("report_id"),
            ))
            continue

        # 指标 ID 非法 / 不在请求范围
        if metric_id not in allowed_metrics:
            result.issues.append(make_issue(
                "METRIC_OUT_OF_RANGE",
                f"metric_id {metric_id} not requested",
                report_id=c.get("report_id"),
            ))
            continue

        # 单位口径不符
        if status == "extracted" and not _unit_ok(metric_id, c.get("raw_unit")):
            result.issues.append(make_issue(
                "UNIT_MISMATCH",
                f"unit {c.get('raw_unit')!r} incompatible with {metric_id}",
                report_id=c.get("report_id"),
            ))
            continue

        # extracted 但 value 为空 → 不允许
        if status == "extracted" and not c.get("value"):
            result.issues.append(make_issue(
                "EXTRACTED_WITHOUT_VALUE",
                f"{metric_id}/{year} status=extracted but value empty",
                report_id=c.get("report_id"),
            ))
            continue

        # unknown/conflict/parse_error 必须有原因
        if status in ("unknown", "conflict", "parse_error") and not note.strip():
            result.issues.append(make_issue(
                "UNKNOWN_WITHOUT_REASON",
                f"{metric_id}/{year} status={status} requires extraction_note",
                report_id=c.get("report_id"),
            ))
            continue

        # value/status 一致性：unknown/parse_error 的 value 必须 null
        if status in ("unknown", "parse_error") and c.get("value") is not None:
            result.issues.append(make_issue(
                "STATUS_VALUE_MISMATCH",
                f"{metric_id}/{year} status={status} must have null value",
                report_id=c.get("report_id"),
            ))
            continue

        result.accepted.append(c)

    return result
