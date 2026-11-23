"""检索（Task 2）：中文别名召回 + Top-5 + 脚注关联。

先用可测的归一化子串召回（不依赖 FTS5 中文分词是否可用）；
FTS5/分词作为增量增强，不作为知识库成立前提（handoff §检索）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 版本化指标词典：优先从配置加载，换报告/换口径只改配置不改代码。
_DICT_PATH = Path(__file__).resolve().parent / "rules" / "metric-dictionary.json"


_FALLBACK_ALIASES = {"E01": ["温室气体排放量"], "E03": ["范围3温室气体排放"],
                     "E04": ["能源总耗量"], "E05": ["总取水量"], "E06": ["总耗水量"]}
_FALLBACK_NOTES = {"E03": ["范围3温室气体"]}


def _load_dictionary() -> tuple[dict[str, list[str]], dict[str, list[str]], bool]:
    """返回 (aliases, notes, is_fallback)。降级必须可见，不静默。"""
    try:
        cfg = json.loads(_DICT_PATH.read_text(encoding="utf-8"))
        aliases = {k: v["aliases"] for k, v in cfg["metrics"].items()}
        notes = dict(cfg.get("footnote_links", {}))
        return aliases, notes, False
    except Exception as exc:
        # 配置缺失/损坏：退回最小默认，但标记 is_fallback=True，由上层暴露 issue
        return dict(_FALLBACK_ALIASES), dict(_FALLBACK_NOTES), True


METRIC_ALIASES, FOOTNOTE_LINK_RULES, DICTIONARY_IS_FALLBACK = _load_dictionary()
DICTIONARY_VERSION = "metric-dict-v0.1"


def reload_dictionary() -> None:
    """重新加载词典（测试/配置热更新用）。"""
    global METRIC_ALIASES, FOOTNOTE_LINK_RULES, DICTIONARY_IS_FALLBACK
    METRIC_ALIASES, FOOTNOTE_LINK_RULES, DICTIONARY_IS_FALLBACK = _load_dictionary()


@dataclass
class Evidence:
    evidence_id: str
    report_id: str
    pdf_page: int
    printed_page: Optional[str]
    kind: str  # text/table/footnote
    text: str
    bbox: Optional[list] = None
    table_id: Optional[str] = None
    row_label: Optional[str] = None
    column_year: Optional[int] = None
    linked_evidence_ids: list[str] = field(default_factory=list)
    viewer_url: str = ""

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id, "report_id": self.report_id,
            "pdf_page": self.pdf_page, "printed_page": self.printed_page,
            "kind": self.kind, "text": self.text, "bbox": self.bbox,
            "table_id": self.table_id, "row_label": self.row_label,
            "column_year": self.column_year,
            "linked_evidence_ids": self.linked_evidence_ids,
            "viewer_url": self.viewer_url,
        }


class EvidenceStore:
    def __init__(self, evidence: list[Evidence]):
        self.items = list(evidence)
        self.by_id = {e.evidence_id: e for e in self.items}

    def add(self, e: Evidence) -> None:
        self.items.append(e)
        self.by_id[e.evidence_id] = e


def _normalize(s: str) -> str:
    """归一化：去空白/标点/繁简常见差异，便于子串召回。"""
    s = s.lower()
    s = re.sub(r"[\s,，。.、:：;；/／()（）]", "", s)
    return s


def alias_recall(text: str, metric_id: str) -> bool:
    """判断 text 是否命中 metric_id 的中文别名。"""
    aliases = METRIC_ALIASES.get(metric_id, [])
    nt = _normalize(text)
    for alias in aliases:
        if _normalize(alias) in nt:
            return True
    return False


def _relevance(query: str, evidence: Evidence, metric_id: Optional[str]) -> float:
    nq = _normalize(query)
    nt = _normalize(evidence.text)
    score = 0.0
    # 直接子串命中
    if nq and nq in nt:
        score += 3.0
    # query 分词后逐项命中
    tokens = [t for t in re.split(r"[\s,，。、]", query) if len(t) >= 2]
    for t in tokens:
        if _normalize(t) in nt:
            score += 0.5
    # 指标别名加权
    if metric_id:
        for alias in METRIC_ALIASES.get(metric_id, []):
            if _normalize(alias) in nt:
                score += 2.0
    return score


def search_evidence(store: EvidenceStore, query: str,
                    metric_id: Optional[str] = None,
                    limit: int = 10) -> dict:
    """返回 Top-N 证据 + 脚注关联 + 问题列表。"""
    scored: list[tuple[float, Evidence]] = []
    for e in store.items:
        # 指标过滤：仅保留命中该指标别名的证据（脚注除外，脚注走关联）
        if metric_id and e.kind != "footnote" and not alias_recall(e.text, metric_id):
            continue
        sc = _relevance(query, e, metric_id)
        if sc > 0:
            scored.append((sc, e))

    scored.sort(key=lambda p: -p[0])
    # 正文证据占 limit 名额；脚注额外附加，不参与 limit 截断
    top = [e for _, e in scored[:limit]]

    issues = []
    if DICTIONARY_IS_FALLBACK:
        issues.append({"code": "DICTIONARY_FALLBACK",
                       "message": "metric dictionary failed to load; using minimal fallback aliases",
                       "report_id": None, "pdf_page": None, "record_id": None})

    # 显式引用优先；同页匹配表格的脚注也随原文命中返回。
    selected_body = [e for e in top if e.kind != "footnote"]
    selected_notes = [e for e in top if e.kind == "footnote"]
    present = {e.evidence_id for e in top}
    contexts = {(e.report_id, e.pdf_page) for e in top if e.kind != "footnote"}
    parents = [e for _, e in scored if e.kind != "footnote" and (e.report_id, e.pdf_page) in contexts]
    for parent in parents:
        for linked_id in parent.linked_evidence_ids:
            note = store.by_id.get(linked_id)
            if (note is not None and note.kind == "footnote" and note.report_id == parent.report_id
                    and linked_id not in present):
                selected_notes.append(note)
                present.add(linked_id)
    # 兼容旧证据的词典关联，限定同一报告与命中页面，避免跨报告串位。
    if metric_id:
        notes = FOOTNOTE_LINK_RULES.get(metric_id, [])
        for e in store.items:
            if (e.kind == "footnote" and (e.report_id, e.pdf_page) in contexts
                    and any(_normalize(n) in _normalize(e.text) for n in notes)
                    and e.evidence_id not in present):
                selected_notes.append(e)
                present.add(e.evidence_id)

    # 正文已占满 20 个名额时仍须给关联脚注留位，至少保留首条正文。
    note_budget = 20 - min(1, len(selected_body))
    notes = selected_notes[:note_budget]
    top = selected_body[:20 - len(notes)] + notes
    if len(selected_body) + len(selected_notes) > 20:
        issues.append({"code": "EVIDENCE_RESULT_LIMIT", "message": "result limit reached; refine the query",
                       "report_id": None, "pdf_page": None, "record_id": None})

    if not top:
        issues.append({"code": "NO_EVIDENCE", "message": "no evidence matched query",
                       "report_id": None, "pdf_page": None, "record_id": None})

    # 正文与额外脚注共同受契约总数上限 20 约束。
    return {"evidence": top, "issues": issues}
