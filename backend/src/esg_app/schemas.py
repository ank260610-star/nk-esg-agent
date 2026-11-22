"""契约 0.2.0 的数据结构（Task 3）。

MetricCandidate → MetricRecord 由服务端补全 record_id/task_id/revision/
review_state/is_current；模型绝不能提供这些服务端字段。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Any

VALID_METRIC_IDS = frozenset({"E01", "E02", "E03", "E04", "E05", "E06"})

# 每个指标的合法单位标记（用于 validate_candidates 的单位一致性校验）。
# 换报告/换口径优先改 rules/metric-dictionary.json 的 unit_hint，此处为最小守门。
UNIT_MARKERS: dict[str, list[str]] = {
    "E01": ["吨二氧化碳当量", "tco2e", "tco₂e", "二氧化碳当量"],
    "E02": ["吨二氧化碳当量/", "tco2e/", "二氧化碳当量/", "排放密度", "排放强度"],
    "E03": ["吨二氧化碳当量", "tco2e", "tco₂e", "二氧化碳当量"],
    "E04": ["兆瓦时", "mwh", "千瓦时", "kwh", "标准煤"],
    "E05": ["立方米", "m3", "m³", "吨"],
    "E06": ["立方米", "m3", "m³", "吨"],
}


def make_issue(code: str, message: str, report_id=None, pdf_page=None, record_id=None) -> dict:
    """构造契约 Issue（所有字段必填，可空值用 None）。"""
    return {"code": code, "message": message, "report_id": report_id,
            "pdf_page": pdf_page, "record_id": record_id}


@dataclass
class ExtractionRun:
    run_id: str
    extractor_version: str
    model_id: Optional[str]
    prompt_version: Optional[str]
    metric_ids: list[str]
    years: list[int]
    usage: dict
    record_ids: list[str]
    owner: str = "backend"

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "owner": self.owner,
            "extractor_version": self.extractor_version, "model_id": self.model_id,
            "prompt_version": self.prompt_version, "metric_ids": self.metric_ids,
            "years": self.years, "usage": self.usage, "record_ids": self.record_ids,
        }


@dataclass
class ExtractionResult:
    status: str  # completed / partial
    records: list[dict]
    issues: list[dict]
    run: ExtractionRun

    def to_dict(self) -> dict:
        return {"status": self.status, "records": self.records,
                "issues": self.issues, "run": self.run.to_dict()}
