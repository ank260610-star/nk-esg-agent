"""通用「文字坐标→行列还原」伪表格引擎（Task 2）。

设计目标：处理「视觉是表格、PDF 内部是文字流」的中文 ESG 报告。
不硬编码任何具体页码/数值/企业——换报告主要改指标词典配置。

核心算法：
1. cluster_rows：按 word.top 容差聚类成行
2. detect_column_anchors：按 x 间隙找列锚点
3. reconstruct_table：从表头识别年份列 → 每行按 x 就近归入年度列
4. tables_equivalent：跨页续表须核对表头/年份/单位，不能只看列数
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

YEAR_RE = re.compile(r"(19|20)\d{2}\s*年?")
PURE_YEAR_RE = re.compile(r"^((?:19|20)\d{2})\s*年?$")


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def xcenter(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def ycenter(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class TableRow:
    label: str = ""
    unit: Optional[str] = None
    values: dict[int, str] = field(default_factory=dict)


@dataclass
class TableGrid:
    years: list[int] = field(default_factory=list)
    rows: list[TableRow] = field(default_factory=list)
    header_fingerprint: str = ""

    @property
    def columns(self) -> list[int]:
        return list(self.years)


def cluster_rows(words: list[Word], y_tolerance: float = 3.0) -> list[list[Word]]:
    """按垂直中心聚类成行；容差内视为同一行。"""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: w.ycenter)
    rows: list[list[Word]] = []
    current: list[Word] = [ordered[0]]
    for w in ordered[1:]:
        if abs(w.ycenter - current[-1].ycenter) <= y_tolerance:
            current.append(w)
        else:
            rows.append(sorted(current, key=lambda x: x.x0))
            current = [w]
    rows.append(sorted(current, key=lambda x: x.x0))
    return rows


def detect_column_anchors(words: list[Word], min_gap: float = 20.0) -> list[tuple[float, float]]:
    """按 x 方向空隙检出列区间 (x_start, x_end)。

    取所有 word 的 x0/x1，按 x0 排序后，相邻 word 间隙 >= min_gap 视为列边界。
    """
    if not words:
        return []
    ordered = sorted(words, key=lambda w: w.x0)
    # 合并水平重叠的 word 成"块"
    blocks: list[list[float]] = []
    for w in ordered:
        if blocks and w.x0 <= blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], w.x1)
        else:
            blocks.append([w.x0, w.x1])
    # 按间隙切列
    anchors: list[tuple[float, float]] = []
    start = blocks[0][0]
    prev_end = blocks[0][1]
    for bstart, bend in blocks[1:]:
        if bstart - prev_end >= min_gap:
            anchors.append((start, prev_end))
            start = bstart
        prev_end = max(prev_end, bend)
    anchors.append((start, prev_end))
    return anchors


def _year_from_token(text: str) -> Optional[int]:
    m = PURE_YEAR_RE.match(text.strip())
    if m:
        y = int(m.group(1))
        if 1900 <= y <= 2100:
            return y
    return None


def _find_header(rows: list[list[Word]]) -> tuple[int, list[tuple[int, float]]]:
    """找表头行，返回 (表头行索引, [(年份, 该年列x中心), ...])。"""
    best_idx = -1
    best_pairs: list[tuple[int, float]] = []
    for idx, row in enumerate(rows[:6]):  # 表头通常在顶部 6 行
        pairs: list[tuple[int, float]] = []
        for w in row:
            y = _year_from_token(w.text)
            if y is not None:
                pairs.append((y, w.xcenter))
        # 去重同一年份（保留最靠右的），按 x 排序
        seen: dict[int, float] = {}
        for y, x in pairs:
            seen[y] = x
        pairs = sorted(seen.items(), key=lambda p: p[1])
        if len(pairs) > len(best_pairs):
            best_idx = idx
            best_pairs = pairs
    return best_idx, best_pairs


def _assign_value_to_year(xcenter: float, year_anchors: list[tuple[int, float]]) -> Optional[int]:
    """按 x 就近把值归入年度列；返回 None 表示不是数据列。"""
    if not year_anchors:
        return None
    # 就近匹配
    nearest = min(year_anchors, key=lambda p: abs(p[1] - xcenter))
    # 若距离过大（超过中位列距的一半），视为不在任何年度列
    xs = sorted(a[1] for a in year_anchors)
    if len(xs) >= 2:
        gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        threshold = sum(gaps) / len(gaps) * 0.9
        if abs(nearest[1] - xcenter) > threshold:
            return None
    return nearest[0]


def _looks_like_value(text: str) -> bool:
    """判断是否数值型单元格（含数字/千分位/小数/负号/百分号/占位符）。

    只有数值词才归入年度列；指标名与单位是文字词，不能塞进数据列。
    占位符 "/" "—" "-" "na" 视为"未披露"值，同样归入年度列并原样保留。
    """
    t = text.strip()
    if not t:
        return False
    if t in ("/", "—", "-", "--", "n/a", "N/A", "na", "NA", "未披露"):
        return True
    # 至少含一个数字，且主体由数字/千分位/小数/百分号/科学计数构成
    if not re.search(r"\d", t):
        return False
    cleaned = re.sub(r"[0-9,，.\-%+eE]", "", t)
    return cleaned == ""


def _looks_like_unit(text: str) -> bool:
    t = text.strip()
    if not t or t in ("/", "—", "-"):
        return False
    unit_markers = ["吨", "兆瓦时", "立方米", "万元", "千元", "元", "千瓦时",
                    "标万立方米", "升", "万升", "标准煤", "二氧化碳当量", "%",
                    "CO2", "CO₂", "MWh", "m3", "m³", "tCO", "标万"]
    return any(m in t for m in unit_markers)


def reconstruct_table(words: list[Word], y_tolerance: float = 3.0) -> TableGrid:
    """从带坐标的 words 还原成 TableGrid。

    不依赖 extract_tables()，专治文字排版伪表格。
    """
    rows = cluster_rows(words, y_tolerance=y_tolerance)
    header_idx, year_pairs = _find_header(rows)
    grid = TableGrid(years=[y for y, _ in year_pairs])

    if not year_pairs:
        # 无年度表头：退化为把整行文本当 row_label 的纯文本证据
        for row in rows:
            grid.rows.append(TableRow(label=" ".join(w.text for w in row)))
        grid.header_fingerprint = "|".join(w.text for w in rows[0]) if rows else ""
        return grid

    # 表头指纹（用于跨页续表判定）
    grid.header_fingerprint = "|".join(
        sorted(f"{y}@{round(x)}" for y, x in year_pairs)
    )

    label_x = min(w.x0 for row in rows for w in row)
    # 单位列 x：表头中没有年份、且紧邻指标列右侧的列
    unit_x: Optional[float] = None
    if header_idx >= 0:
        non_year_in_header = [w for w in rows[header_idx]
                              if _year_from_token(w.text) is None
                              and w.text.strip() not in ("指标", "")]
        if non_year_in_header:
            unit_x = min(w.xcenter for w in non_year_in_header)

    for ridx, row in enumerate(rows):
        if ridx == header_idx:
            continue
        table_row = TableRow()
        val_words = []
        for w in row:
            y = _assign_value_to_year(w.xcenter, year_pairs)
            is_year_token = y is not None and PURE_YEAR_RE.match(w.text.strip())
            # 年份表头 token 跳过
            if ridx == header_idx:
                continue
            if y is not None and not is_year_token and _looks_like_value(w.text):
                # 只有数值/占位词才归入年度数据列
                val_words.append((y, w))
            else:
                # 文字词：指标名 or 单位（永不当数值塞进年度列）
                if _looks_like_unit(w.text):
                    table_row.unit = w.text.strip()
                elif w.text.strip() in ("指标", "单位", "") or PURE_YEAR_RE.match(w.text.strip()):
                    continue  # 表头残留词跳过
                elif not table_row.label:
                    table_row.label = w.text.strip()
                else:
                    table_row.label += " " + w.text.strip()
        for y, w in val_words:
            # "/" 必须原样保留
            table_row.values[y] = w.text.strip()
        if table_row.label or table_row.values:
            grid.rows.append(table_row)

    return grid


def tables_equivalent(a: TableGrid, b: TableGrid) -> bool:
    """跨页续表判定：表头年份指纹 + 单位集合相同才可续。

    明确禁止"列数相同就续表"。
    """
    if a.years != b.years:
        return False
    if a.header_fingerprint != b.header_fingerprint:
        return False
    units_a = sorted({r.unit for r in a.rows if r.unit})
    units_b = sorted({r.unit for r in b.rows if r.unit})
    return units_a == units_b


def make_evidence_page_ref(page_index_0based: int, printed_page: Optional[str]) -> dict:
    """把解析器 0 基页索引转成 1 基 PDF 物理页；印刷页另存不混用。"""
    return {
        "pdf_page": page_index_0based + 1,
        "printed_page": printed_page,
    }
