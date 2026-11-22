"""Task 2: 通用「文字坐标→行列还原」伪表格引擎测试。

不依赖真实 PDF，用合成 words 验证解析逻辑本身。
关键约束（handoff Task 2）：年度列不串行、保留"/"、单位不混用、
跨页续表须核对表头/年份/单位而非只看列数、印刷页与 PDF 页分离。
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.parsing import (
    Word,
    cluster_rows,
    detect_column_anchors,
    reconstruct_table,
    tables_equivalent,
    make_evidence_page_ref,
)


def W(text, x, top, width=None, height=11):
    """合成一个 pdfplumber 风格的 word。width 按字符数估算或显式给定。"""
    w = width if width is not None else max(4, len(text) * 6)
    return Word(text=text, x0=float(x), x1=float(x + w),
                top=float(top), bottom=float(top + height))


class TestClusterRows:
    def test_same_top_groups_into_one_row(self):
        words = [W("能源总耗量", 0, 10), W("兆瓦时", 200, 10.5),
                 W("23,552,569", 400, 10.2)]
        rows = cluster_rows(words, y_tolerance=3.0)
        assert len(rows) == 1
        assert len(rows[0]) == 3

    def test_different_top_separates_rows(self):
        words = [W("能源总耗量", 0, 10), W("兆瓦时", 200, 30)]
        rows = cluster_rows(words, y_tolerance=3.0)
        assert len(rows) == 2


class TestColumnAnchors:
    def test_detects_four_columns_from_x_gaps(self):
        words = [W("指标", 0, 10), W("单位", 200, 10),
                 W("2023年", 400, 10), W("2024年", 600, 10)]
        anchors = detect_column_anchors(words, min_gap=20.0)
        assert len(anchors) == 4


class TestReconstructTable:
    def _sample_words(self):
        return [
            W("指标", 0, 10), W("单位", 200, 10),
            W("2022年", 350, 10), W("2023年", 480, 10), W("2024年", 610, 10),
            W("能源总耗量", 0, 26), W("兆瓦时", 200, 26),
            W("10,618,556.88", 350, 26), W("16,592,592.44", 480, 26),
            W("23,552,569.28", 610, 26),
            W("能源密度", 0, 42), W("兆瓦时/万元营收", 200, 42),
            W("0.2504", 350, 42), W("0.2755", 480, 42), W("0.3031", 610, 42),
        ]

    def test_years_detected_from_header(self):
        grid = reconstruct_table(self._sample_words())
        assert grid.years == [2022, 2023, 2024]

    def test_year_columns_do_not_shift(self):
        """年度列不串行：2022 的值必须落在 2022 列。"""
        grid = reconstruct_table(self._sample_words())
        row = grid.rows[0]
        assert row.values[2022] == "10,618,556.88"
        assert row.values[2023] == "16,592,592.44"
        assert row.values[2024] == "23,552,569.28"

    def test_slash_missing_value_preserved(self):
        """'/' 表示未披露，必须原样保留，不能被当空值丢弃。"""
        words = [
            W("指标", 0, 10), W("2023年", 300, 10), W("2024年", 450, 10),
            W("范围3排放量", 0, 26), W("/", 300, 26), W("3,326,312.49", 450, 26),
        ]
        grid = reconstruct_table(words)
        row = grid.rows[0]
        assert row.values[2023] == "/"
        assert row.values[2024] == "3,326,312.49"

    def test_units_not_mixed_across_rows(self):
        """能源行与密度行单位不同，不得混用。"""
        grid = reconstruct_table(self._sample_words())
        assert grid.rows[0].unit == "兆瓦时"
        assert grid.rows[1].unit == "兆瓦时/万元营收"

    def test_water_and_energy_units_not_confused(self):
        """水量(m3)与能源(MWh)单位不得混用到同一行。"""
        words = [
            W("指标", 0, 10), W("单位", 180, 10), W("2024年", 400, 10),
            W("总取水量", 0, 26), W("立方米", 180, 26), W("94,268,627", 400, 26),
            W("能源总耗量", 0, 42), W("兆瓦时", 180, 42), W("23,552,569", 400, 42),
        ]
        grid = reconstruct_table(words)
        assert grid.rows[0].unit == "立方米"
        assert grid.rows[1].unit == "兆瓦时"


class TestTableContinuation:
    def test_same_header_allows_continuation(self):
        a = reconstruct_table([
            W("指标", 0, 10), W("2023年", 300, 10), W("2024年", 450, 10),
            W("总取水量", 0, 26), W("73,807,222", 300, 26), W("94,268,627", 450, 26),
        ])
        b = reconstruct_table([
            W("指标", 0, 10), W("2023年", 300, 10), W("2024年", 450, 10),
            W("总排水量", 0, 26), W("51,739,770", 300, 26), W("38,950,920", 450, 26),
        ])
        assert tables_equivalent(a, b) is True

    def test_same_column_count_but_different_header_blocks_continuation(self):
        """列数相同但表头/年份不同 → 不得续表（handoff 明确要求）。"""
        a = reconstruct_table([
            W("指标", 0, 10), W("2023年", 300, 10), W("2024年", 450, 10),
            W("总取水量", 0, 26), W("73,807,222", 300, 26), W("94,268,627", 450, 26),
        ])
        b = reconstruct_table([
            W("指标", 0, 10), W("2022年", 300, 10), W("2023年", 450, 10),
            W("总取水量", 0, 26), W("51,107,571", 300, 26), W("73,807,222", 450, 26),
        ])
        assert tables_equivalent(a, b) is False


class TestPageRef:
    def test_pdf_page_is_one_based_and_printed_page_separate(self):
        """pdfplumber 0基索引→1基PDF页；印刷页另存，不透传 page.number。"""
        ref = make_evidence_page_ref(page_index_0based=124, printed_page="126")
        assert ref["pdf_page"] == 125
        assert ref["printed_page"] == "126"

    def test_printed_page_null_when_unknown(self):
        ref = make_evidence_page_ref(page_index_0based=0, printed_page=None)
        assert ref["pdf_page"] == 1
        assert ref["printed_page"] is None
