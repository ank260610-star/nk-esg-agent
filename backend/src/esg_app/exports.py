"""同版本导出（Task 6）：Markdown / CSV / JSON 三格式绑定同一 revision。

安全边界：
- 缺失值不写 0；"/" 等占位符保留原文。
- CSV 公式注入（= + - @ 及制表/回车开头）加单引号前缀转义；
  正常负数（如 -18.15）保持数值语义不被破坏。
- artifact_id 由服务端生成；下载只按登记的 artifact_id 查表，
  不接受任何路径片段（含 ..、反斜杠、绝对路径）。
- 任务 revision 变化后旧 artifact 标 is_current=false，仍可下载但明确过期。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from pathlib import Path
from typing import Optional

# 只有中段为负号且整体是合法数值时才视为数字（不加引号）。
_NUMERIC_RE = re.compile(r"^-?(?:\d[\d,]*)?(?:\.\d+)?$")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class ExportError(ValueError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def csv_safe_cell(value) -> str:
    """CSV 单元格：None→空串；公式注入转义；数字/占位符原样。"""
    if value is None:
        return ""
    s = str(value)
    if not s:
        return s
    if s[0] in _FORMULA_PREFIXES:
        # 负数是合法数值语义（同比 -18.15%），不当公式处理
        if s[0] == "-" and _NUMERIC_RE.match(s):
            return s
        return "'" + s
    return s


class ArtifactStore:
    """登记产物：仅以 artifact_id 为键，目录/文件名由服务端生成。"""

    def __init__(self, root: Path | str, repository=None):
        self.repository = repository
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._meta: dict[str, dict] = {}
        self._task_artifacts: dict[str, list[str]] = {}

    def add(self, task_id: str, fmt: str, media_type: str,
            filename: str, content: bytes, revision: int) -> dict:
        # 文件名只取 basename 且限安全字符，双重防止路径穿越
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(filename).name)
        if not safe_name or safe_name in (".", ".."):
            raise ExportError("BAD_FILENAME")
        artifact_id = f"art_{uuid.uuid4().hex[:24]}"
        # 文件名携带唯一 ID：同 revision 重复导出不覆盖旧文件，sha256 始终对应本文件内容
        stem, dot, ext = safe_name.rpartition(".")
        unique_name = f"{stem}_{artifact_id}{dot}{ext}" if dot else f"{safe_name}_{artifact_id}"
        path = self.root / unique_name
        path.write_bytes(content)
        meta = {
            "artifact_id": artifact_id,
            "format": fmt,
            "media_type": media_type,
            "filename": unique_name,
            "download_url": f"/api/artifacts/{artifact_id}",
            "revision": revision,
            "sha256": hashlib.sha256(content).hexdigest(),
            "task_id": task_id,
            "is_current": True,
            "_path": str(path),
        }
        if self.repository is not None:
            with self.repository.transaction() as db:
                db.execute('INSERT INTO artifacts VALUES(?,?,?)', (artifact_id, task_id, json.dumps(meta)))
        self._meta[artifact_id] = meta
        self._task_artifacts.setdefault(task_id, []).append(artifact_id)
        return self.public(meta)

    @staticmethod
    def public(meta: dict) -> dict:
        """对外契约字段（additionalProperties:false），内部字段不泄露。"""
        keys = ("artifact_id", "format", "media_type", "filename",
                "download_url", "revision", "sha256")
        return {k: meta[k] for k in keys}

    def get_meta(self, artifact_id: str | None) -> Optional[dict]:
        if not artifact_id or not isinstance(artifact_id, str):
            return None
        # 严格 ID 校验：任何路径片段、斜杠、.. 直接拒绝
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", artifact_id):
            return None
        if self.repository is not None:
            with self.repository.transaction() as db:
                row = db.execute('SELECT metadata FROM artifacts WHERE artifact_id=?', (artifact_id,)).fetchone()
                if not row:
                    return None
                meta = json.loads(row[0])
                task = db.execute('SELECT revision FROM tasks WHERE task_id=?', (meta['task_id'],)).fetchone()
                meta['is_current'] = bool(task and task[0] == meta['revision'])
                return meta
        return self._meta.get(artifact_id)

    def is_current(self, artifact_id: str) -> bool:
        meta = self.get_meta(artifact_id)
        return bool(meta and meta["is_current"])

    def get_content(self, artifact_id: str | None) -> Optional[bytes]:
        meta = self.get_meta(artifact_id)
        if meta is None:
            return None
        path = Path(meta["_path"]).resolve()
        root = self.root.resolve()
        if root not in path.parents:
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def invalidate_task(self, task_id: str) -> None:
        for aid in self._task_artifacts.get(task_id, []):
            self._meta[aid]["is_current"] = False

    def list_for_task(self, task_id: str) -> list[dict]:
        if self.repository is not None:
            with self.repository.transaction() as db:
                task = db.execute('SELECT revision FROM tasks WHERE task_id=?', (task_id,)).fetchone()
                return [{**self.public(meta), 'is_current': bool(task and task[0] == meta['revision'])}
                        for (raw,) in db.execute('SELECT metadata FROM artifacts WHERE task_id=?', (task_id,))
                        for meta in [json.loads(raw)]]
        return [
            {**self.public(self._meta[aid]),
             "is_current": self._meta[aid]["is_current"]}
            for aid in self._task_artifacts.get(task_id, [])
        ]


def _render_markdown(task_id: str, revision: int, records: list[dict],
                     score: dict, checks: list[dict], comparisons: list[dict],
                     evidence: list[dict], rule_version: str,
                     report_meta: list[dict]) -> str:
    lines = [
        "# ESG 披露质量分析报告（自建试验规则）",
        "",
        f"- 任务：{task_id}｜任务 revision：{revision}｜规则版本：{rule_version}",
        "- 规则性质：本项目自建试验方案，非官方评分，不代表合规结论或投资建议。",
        "",
        "## 一、资料与版本",
        "",
    ]
    for r in report_meta:
        lines.append(f"- 报告 {r.get('report_id', '')}｜年度 {r.get('report_year', '')}"
                     f"｜SHA256 {str(r.get('sha256', ''))[:16]}…")
    if not report_meta:
        lines.append("- （本次导出未登记报告元信息）")

    lines += ["", "## 二、六项核心指标", "",
              "| 指标 | 年度 | 原值 | 规范值 | 单位 | 状态 | 证据 |",
              "|---|---|---|---|---|---|---|"]
    for rec in records:
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            rec.get("metric_id", ""), rec.get("metric_year", ""),
            rec.get("raw_value", ""), rec.get("value") or "",
            rec.get("canonical_unit") or rec.get("raw_unit", ""),
            rec.get("status", ""), "、".join(rec.get("evidence_ids", []))))
    if not records:
        lines.append("| — | — | — | — | — | — | — |")

    lines += ["", "## 三、跨年比较", ""]
    for c in comparisons:
        lines.append(f"- {c.get('metric_id', '')} {c.get('older_year', '')}→"
                     f"{c.get('newer_year', '')}：{c.get('status', '')}"
                     f"｜同比 {c.get('percent_change') or '不可用'}")
    if not comparisons:
        lines.append("- （无可比结果；缺失/冲突/未复核均不算同比）")

    lines += ["", "## 四、评分与证据覆盖率", "",
              f"- 覆盖率：{score.get('coverage')}｜参考分：{score.get('reference_score')}｜"
              f"区间：[{score.get('lower_bound')}, {score.get('upper_bound')}]",
              f"- 最终分：{score.get('final_score') or '未定（证据不足）'}｜"
              f"标签：{score.get('label', '')}"]
    for d in score.get("dimension_results", []):
        lines.append(f"- {d.get('dimension')}：覆盖率 {d.get('coverage')}"
                     f"｜参考分 {d.get('reference_score')}")

    lines += ["", "## 五、核查线索与未知项", ""]
    unresolved = [c for c in checks if c.get("status") in ("unknown", "not_met", "partial")]
    for c in unresolved:
        lines.append(f"- {c.get('check_id', '')}：{c.get('status', '')}｜"
                     f"{c.get('missing_information') or c.get('reason') or ''}")
    if not unresolved:
        lines.append("- （无）")

    lines += ["", "## 六、金融影响假设（条件性）", "",
              "- 本报告只评价环境信息披露质量；不输出企业造假判断、合规结论或投资建议。",
              "- 财务影响仅在用户提供可核对财务背景且明确假设时，作为条件性线索呈现。",
              "", "## 七、来源与局限", "",
              "- 来源：登记报告的 PDF 证据（页码见证据登记），规则来源多为待核实的定位线索。",
              "- 局限：解析失败≠未披露；unknown 不计 0 分；N/A 仅人工确认后生效；"
              "单一报告样本不代表行业普遍情况。", ""]
    return "\n".join(lines)


def _render_csv(records: list[dict], dispositions=None) -> str:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["metric_id", "metric_year", "raw_value", "value", "raw_unit",
                "canonical_unit", "status", "origin", "evidence_ids", "extraction_note",
                "review_decision", "review_reason", "search_scope", "missing_material", "use_limitations",
                "review_target_type", "review_target_id", "review_needs_recheck", "review_source_target_id"])
    by_record = {d['target_id']: d for d in (dispositions or []) if d['target_type'] == 'metric'}
    for rec in records:
        review = by_record.get(rec.get('record_id'), {})
        w.writerow([
            csv_safe_cell(rec.get("metric_id")),
            csv_safe_cell(rec.get("metric_year")),
            csv_safe_cell(rec.get("raw_value")),
            csv_safe_cell(rec.get("value")),
            csv_safe_cell(rec.get("raw_unit")),
            csv_safe_cell(rec.get("canonical_unit")),
            csv_safe_cell(rec.get("status")),
            csv_safe_cell(rec.get("origin")),
            csv_safe_cell("、".join(rec.get("evidence_ids", []))),
            csv_safe_cell(rec.get("extraction_note", "")),
            *[csv_safe_cell(review.get(k, "")) for k in ['decision', 'reason', 'search_scope', 'missing_material', 'limitations']],
            'metric', csv_safe_cell(rec.get('record_id', '')),
            csv_safe_cell(review.get('needs_recheck', '')), csv_safe_cell(review.get('source_target_id', '')),
        ])
    for review in dispositions or []:
        if review['target_type'] == 'check':
            w.writerow([''] * 10 + [csv_safe_cell(review.get(k, '')) for k in
                        ['decision', 'reason', 'search_scope', 'missing_material', 'limitations']]
                       + ['check', csv_safe_cell(review['target_id']), csv_safe_cell(review.get('needs_recheck', '')), csv_safe_cell(review.get('source_target_id', ''))])
    return buf.getvalue()


def build_export_bundle(task_id: str, expected_revision: int, current_revision: int,
                        records: list[dict], score: dict, checks: list[dict],
                        comparisons: list[dict], evidence: list[dict],
                        rule_version: str, report_meta: list[dict],
                        store: ArtifactStore, review_dispositions: list[dict] | None = None) -> dict:
    """生成三格式同版本产物；revision 不符则拒绝（409）。"""
    if expected_revision != current_revision:
        raise ExportError("REVISION_CONFLICT",
                          f"expected {expected_revision}, current {current_revision}")
    review_dispositions = review_dispositions or []
    json_payload = {
        "task_id": task_id,
        "revision": current_revision,
        "rule_version": rule_version,
        "experimental": True,
        "review_dispositions": review_dispositions,
        "reports": report_meta,
        "metrics": records,
        "comparisons": comparisons,
        "checks": checks,
        "score": score,
        "evidence_index": [
            {"evidence_id": e.get("evidence_id"), "pdf_page": e.get("pdf_page"),
             "printed_page": e.get("printed_page"), "kind": e.get("kind"),
             "viewer_url": e.get("viewer_url")}
            for e in evidence
        ],
        "financial_impact": "条件性线索，未输出投资建议或合规结论",
        "limitations": ["解析失败不等于未披露", "unknown 不计 0 分",
                        "N/A 须人工确认", "单一样本不代表行业"],
    }
    disposition_lines = ["", "## 人工核查处置与使用限制", "", "核查处置完成不等于数据已经证实；未知项不补零、不作确定同比。", ""]
    for item in review_dispositions:
        for field in ['target_type', 'target_id', 'decision', 'reason', 'search_scope', 'missing_material', 'limitations', 'actor', 'at', 'revision', 'needs_recheck', 'source_target_id', 'resolution_evidence_ids']:
            if field in item:
                disposition_lines.append(f"- {field}: {item[field]}")
    contents = [
        ("markdown", "text/markdown", "md",
         _render_markdown(task_id, current_revision, records, score, checks,
                          comparisons, evidence, rule_version, report_meta) + "\n".join(disposition_lines)),
        ("csv", "text/csv", "csv", _render_csv(records, review_dispositions)),
        ("json", "application/json", "json",
         json.dumps(json_payload, ensure_ascii=False, indent=2, default=str)),
    ]
    artifacts = []
    for fmt, media_type, ext, text in contents:
        content = text.encode("utf-8")
        filename = f"esg-{task_id}-r{current_revision}.{ext}"
        artifacts.append(store.add(task_id, fmt, media_type, filename,
                                   content, current_revision))
    return {"artifacts": artifacts, "rule_version": rule_version,
            "revision": current_revision}
