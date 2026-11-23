"""指标提取主入口（Task 3）。

extract_metrics 由后端调用：
- 所有输入以服务端任务为准（metric_ids/years/allowed_evidence_ids 由后端传入），
  模型只产出候选，不能自行决定 task_id/revision/证据范围。
- revision 变化 → 拒绝提交（防并发写）。
- 幂等 key 命中缓存 → 不重复调用模型。
- 人工已确认的记录键 → 跳过模型候选，不覆盖。
- 模型配置（model_id/prompt_version）由后端控制，不接收外部传入。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional, Callable

from .schemas import ExtractionResult, ExtractionRun, make_issue
from .metrics import validate_candidates


@dataclass
class ExtractionDeps:
    """后端注入的依赖：模型调用替身 + 版本配置。"""
    model_fn: Callable[[dict], list[dict]]
    extractor_version: str
    model_id: Optional[str]
    prompt_version: Optional[str]


class RevisionChanged(Exception):
    """提取过程中任务 revision 变化，拒绝提交。"""


def _to_record(candidate: dict, task_id: str, revision: int) -> dict:
    """把合法候选升级为 MetricRecord，补服务端字段。"""
    rec = dict(candidate)  # 保留所有候选字段
    rec["record_id"] = f"mr_{uuid.uuid4().hex[:12]}"
    rec["task_id"] = task_id
    rec["review_state"] = "unreviewed"
    rec["revision"] = revision
    rec["is_current"] = True
    # origin=manual 是模型禁止输出的；服务端强制规范化为 reported/derived
    if rec.get("origin") not in ("reported", "derived"):
        rec["origin"] = "reported"
    return rec


def extract_metrics(
    task_id: str,
    expected_revision: int,
    current_revision: int,
    metric_ids: list[str],
    years: list[int],
    allowed_evidence_ids: set[str],
    deps: ExtractionDeps,
    idempotency_cache: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
    confirmed_record_keys: Optional[set[tuple]] = None,
) -> ExtractionResult:
    """执行指标提取，返回契约 ExtractionResult。"""
    # 1. revision 并发校验
    if expected_revision != current_revision:
        raise RevisionChanged(
            f"revision changed: expected {expected_revision}, current {current_revision}"
        )

    # 2. 幂等：命中缓存直接返回，不重复调用模型
    if idempotency_cache is not None and idempotency_key:
        cached = idempotency_cache.get(idempotency_key)
        if cached is not None:
            # 返回时 usage.model_calls=0，表示复用缓存
            run = ExtractionRun(
                run_id=cached["run_id"], extractor_version=deps.extractor_version,
                model_id=deps.model_id, prompt_version=deps.prompt_version,
                metric_ids=metric_ids, years=years,
                usage={"model_calls": 0, "input_tokens": 0, "output_tokens": 0},
                record_ids=cached["record_ids"],
            )
            return ExtractionResult(status="completed", records=cached["records"],
                                    issues=[], run=run)

    # 3. 调用模型替身（模型配置由后端 deps 控制）
    ctx = {
        "task_id": task_id, "metric_ids": metric_ids, "years": years,
        "allowed_evidence_ids": sorted(allowed_evidence_ids),
        "model_id": deps.model_id, "prompt_version": deps.prompt_version,
    }
    raw_candidates = deps.model_fn(ctx) or []

    # 4. 人工确认保护：过滤掉与已确认 (metric_id, year) 冲突的模型候选
    confirmed = confirmed_record_keys or set()
    skipped_manual = False
    if confirmed:
        filtered = []
        for c in raw_candidates:
            key = (c.get("metric_id"), c.get("metric_year"))
            if key in confirmed:
                skipped_manual = True
                continue
            filtered.append(c)
        raw_candidates = filtered

    # 5. 内部候选校验
    validation = validate_candidates(
        candidates=raw_candidates,
        allowed_years=years,
        allowed_evidence_ids=allowed_evidence_ids,
        allowed_metric_ids=set(metric_ids),
    )

    issues = list(validation.issues)
    if skipped_manual:
        issues.append(make_issue(
            "MANUAL_CONFIRMED_SKIPPED",
            "model candidates for manually confirmed (metric,year) were skipped",
            report_id=None,
        ))

    # 6. 生成服务端字段
    records = [_to_record(c, task_id, expected_revision) for c in validation.accepted]

    # 7. 覆盖检查：每个 metric×year 组合必须有候选或明确 issue
    covered = {(r["metric_id"], r["metric_year"]) for r in records}
    for mid in metric_ids:
        for yr in years:
            if (mid, yr) not in covered:
                issues.append(make_issue(
                    "MISSING_COMBINATION",
                    f"no accepted candidate for {mid}/{yr} (unknown or rejected)",
                    report_id=None,
                ))

    status = "completed" if records and not validation.batch_rejected else "partial"
    run = ExtractionRun(
        run_id=f"run_{uuid.uuid4().hex[:12]}",
        extractor_version=deps.extractor_version,
        model_id=deps.model_id, prompt_version=deps.prompt_version,
        metric_ids=metric_ids, years=years,
        usage={"model_calls": 1, "input_tokens": None, "output_tokens": None},
        record_ids=[r["record_id"] for r in records],
    )
    result = ExtractionResult(status=status, records=records, issues=issues, run=run)

    # 8. 写幂等缓存
    if idempotency_cache is not None and idempotency_key:
        idempotency_cache[idempotency_key] = {
            "records": records, "run_id": run.run_id,
            "record_ids": run.record_ids,
        }

    return result
