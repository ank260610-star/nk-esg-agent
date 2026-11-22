"""Task 2: 检索（中文别名召回/Top-5/脚注关联/失败页问题列表）测试。"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.retrieval import (
    EvidenceStore,
    Evidence,
    alias_recall,
    search_evidence,
)


def ev(eid, text, page, kind="text", linked=None):
    return Evidence(evidence_id=eid, report_id="r1", pdf_page=page,
                    printed_page=str(page - 1), kind=kind, text=text,
                    bbox=None, table_id=None, row_label=None,
                    column_year=None, linked_evidence_ids=linked or [],
                    viewer_url=f"/api/reports/r1/pdf#page={page}")


SAMPLE = [
    ev("e_e01", "范围1温室气体排放量 吨二氧化碳当量 1,539,251.46", 127, "table"),
    ev("e_e01b", "范围1+范围2温室气体排放量 12,341,455.00", 127, "table"),
    ev("e_e03", "范围3温室气体排放量 3,326,312.49", 127, "table"),
    ev("e_fn21", "范围3温室气体排放量包括员工差旅、员工通勤、与燃料和能源相关的活动", 127, "footnote"),
    ev("e_e04", "能源总耗量 兆瓦时 23,552,569.28", 125, "table"),
    ev("e_e05", "总取水量 立方米 94,268,627", 126, "table"),
    ev("e_bad", "字体乱码页", 88, "text"),
]


class TestAliasRecall:
    def test_chinese_alias_maps_to_metric(self):
        """中文别名可召回：范围1排放 / 温室气体都指向 E01。"""
        assert alias_recall("范围1温室气体排放", "E01") is True
        assert alias_recall("温室气体排放量", "E01") is True
        assert alias_recall("范围一排放", "E01") is True

    def test_wrong_metric_alias_does_not_match(self):
        assert alias_recall("范围1温室气体排放", "E05") is False


class TestSearchEvidence:
    def setup_method(self):
        self.store = EvidenceStore(SAMPLE)

    def test_top5_returns_ranked_evidence(self):
        res = search_evidence(self.store, "温室气体排放", limit=5)
        assert len(res["evidence"]) <= 5
        assert "e_e01" in [e.evidence_id for e in res["evidence"]]

    def test_scope3_brings_footnote21(self):
        """脚注21 随 Scope 3 候选返回。"""
        res = search_evidence(self.store, "范围3温室气体排放", metric_id="E03", limit=5)
        ids = [e.evidence_id for e in res["evidence"]]
        assert "e_e03" in ids
        assert "e_fn21" in ids

    def test_metric_filter_excludes_other_metrics(self):
        res = search_evidence(self.store, "温室气体排放", metric_id="E01", limit=10)
        ids = [e.evidence_id for e in res["evidence"]]
        assert "e_e05" not in ids

    def test_page_ref_in_evidence(self):
        res = search_evidence(self.store, "能源总耗量", metric_id="E04", limit=3)
        hit = next(e for e in res["evidence"] if e.evidence_id == "e_e04")
        assert hit.pdf_page == 125
        assert hit.printed_page == "124"

    def test_footnote_survives_when_hits_equal_limit(self):
        """回归 bug: 正文命中数=limit 时，脚注不得被 [:limit] 截断丢失。"""
        items = [ev(f"e{i}", "范围3温室气体排放量 数据", 127) for i in range(5)]
        items.append(ev("fn21",
                        "范围3温室气体排放量包括员工差旅、员工通勤、与燃料和能源相关的活动",
                        127, "footnote"))
        store = EvidenceStore(items)
        res = search_evidence(store, "范围3温室气体排放", metric_id="E03", limit=5)
        ids = [e.evidence_id for e in res["evidence"]]
        assert "fn21" in ids, "脚注21被limit截断"


class TestDictionaryFallback:
    def test_fallback_is_visible_in_issues(self, monkeypatch, tmp_path):
        """回归: 词典加载失败必须暴露 DICTIONARY_FALLBACK，不静默降级。"""
        import esg_app.retrieval as ret
        # 指向不存在的词典 → 触发 fallback
        monkeypatch.setattr(ret, "_DICT_PATH", tmp_path / "missing.json")
        ret.reload_dictionary()
        store = EvidenceStore([ev("e1", "温室气体排放量 100", 127)])
        res = search_evidence(store, "温室气体排放量", limit=3)
        codes = [i["code"] for i in res["issues"]]
        assert "DICTIONARY_FALLBACK" in codes
        ret.reload_dictionary()  # 恢复
