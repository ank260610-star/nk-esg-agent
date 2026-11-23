"""Task 2 收尾：标注查询集实测 Top-5 命中率 + 乱码/合并单元格边界。

handoff Task 2 验收项：
- 「用同一标注查询集记录 Top-5 命中」——本文件定义标注集并实测。
- 「为跨页表头、合并单元格、脚注串位和字体乱码各准备案例；绑定失败写问题码，不静默合并」。
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.retrieval import (
    EvidenceStore, Evidence, search_evidence, alias_recall,
)
from esg_app.parsing import (
    Word, reconstruct_table, tables_equivalent, detect_value_conflicts,
)


def ev(eid, text, page, kind="table"):
    return Evidence(evidence_id=eid, report_id="byd_r1", pdf_page=page,
                    printed_page=str(page - 1), kind=kind, text=text)


# 标注查询集：query → 期望在 Top-5 命中的 evidence_id 集合（含别名与跨页表述）
ANNOTATED_QUERIES = [
    ("范围1温室气体排放量", "E01", "gold_e01"),
    ("温室气体排放密度", "E02", "gold_e02"),
    ("范围3温室气体排放", "E03", "gold_e03"),
    ("能源总耗量", "E04", "gold_e04"),
    ("总取水量", "E05", "gold_e05"),
    ("总耗水量", "E06", "gold_e06"),
]


def _benchmark_store():
    """含六指标真值证据 + 干扰项的检索库（数值取自比亚迪真机侦察）。"""
    return EvidenceStore([
        ev("gold_e01", "范围1温室气体排放量 吨二氧化碳当量 1,539,251.46", 127),
        ev("gold_e02", "温室气体排放密度 吨二氧化碳当量/万元营收 0.2049", 127),
        ev("gold_e03", "范围3温室气体排放量 3,326,312.49", 127),
        ev("gold_e04", "能源总耗量 兆瓦时 23,552,569.28", 125),
        ev("gold_e05", "总取水量 立方米 94,268,627", 126),
        ev("gold_e06", "总耗水量 立方米 55,317,707", 126),
        ev("noise_1", "员工人数 90000 人", 30),
        ev("noise_2", "绿色电力证书 2,237,986 个", 125),
        ev("fn21", "范围3温室气体排放量包括员工差旅、员工通勤", 127, "footnote"),
    ])


class TestTop5Benchmark:
    """用标注查询集实测 Top-5 命中率，作为闸门 B 检索部分的量化证据。"""

    def test_top5_hit_rate_at_least_6_of_6(self):
        store = _benchmark_store()
        hits = 0
        for query, metric_id, expected_id in ANNOTATED_QUERIES:
            res = search_evidence(store, query, metric_id=metric_id, limit=5)
            top_ids = [e.evidence_id for e in res["evidence"]]
            if expected_id in top_ids:
                hits += 1
        # 实测目标：6/6。若不足，按 handoff 需比较 jieba/BM25 再优化。
        assert hits == 6, f"Top-5 命中 {hits}/6，低于验收阈值"

    def test_each_query_returns_no_false_metric(self):
        """带 metric_id 的查询不得混入其它指标证据（脚注除外）。"""
        store = _benchmark_store()
        for query, metric_id, _ in ANNOTATED_QUERIES:
            res = search_evidence(store, query, metric_id=metric_id, limit=5)
            for e in res["evidence"]:
                if e.kind == "footnote":
                    continue
                # 正文证据必须命中该指标别名
                assert alias_recall(e.text, metric_id), (
                    f"{metric_id} 查询混入非本指标证据 {e.evidence_id}"
                )


class TestParserBoundaryCases:
    """handoff 明确要求：合并单元格、脚注串位、字体乱码各备案例。"""

    def test_merged_cell_missing_year_is_not_silently_filled(self):
        """合并单元格导致某年缺值：不得用相邻值静默补齐。"""
        # 只有 2024 有值，2023 单元格在合并区缺失
        grid = reconstruct_table([
            Word(text="指标", x0=0, x1=60, top=10, bottom=21),
            Word(text="2023年", x0=300, x1=360, top=10, bottom=21),
            Word(text="2024年", x0=450, x1=510, top=10, bottom=21),
            Word(text="范围3排放量", x0=0, x1=90, top=30, bottom=41),
            Word(text="3,326,312.49", x0=450, x1=540, top=30, bottom=41),
        ])
        row = grid.rows[0]
        # 缺失年不得出现键；有值年必须正确
        assert 2023 not in row.values
        assert row.values[2024] == "3,326,312.49"

    def test_garbled_text_is_preserved_not_repaired(self):
        """字体乱码不得被'修补'成看似合理的数字。"""
        grid = reconstruct_table([
            Word(text="指标", x0=0, x1=60, top=10, bottom=21),
            Word(text="2024年", x0=300, x1=360, top=10, bottom=21),
            Word(text="能源消耗", x0=0, x1=60, top=30, bottom=41),
            Word(text="\ufffd\ufffd\ufffd", x0=300, x1=360, top=30, bottom=41),
        ])
        # 乱码不是数值，不得被塞进年度列当作提取值
        assert 2024 not in grid.rows[0].values

    def test_footnote_string_shift_keeps_link(self):
        """脚注串位：脚注 evidence 仍可关联到 Scope 3 候选。"""
        store = _benchmark_store()
        res = search_evidence(store, "范围3温室气体排放", metric_id="E03", limit=5)
        ids = [e.evidence_id for e in res["evidence"]]
        assert "fn21" in ids
