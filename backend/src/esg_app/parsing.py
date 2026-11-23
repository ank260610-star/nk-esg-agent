"""通用「文字坐标→行列还原」伪表格引擎（Task 2）。

设计目标：处理「视觉是表格、PDF 内部是文字流」的中文 ESG 报告。
不硬编码任何具体页码/数值/企业——换报告主要改指标词典配置。

核心算法：
1. cluster_rows：按 word.top 容差聚类成行
2. detect_column_anchors：按 x 间隙找列锚点
3. reconstruct_table：独立表头分栏/分块 → 绑定折行标签、单位和年度单元格
4. extract_footnotes：识别编号定义及文内引用，保留证据关联
5. tables_equivalent：跨页续表须核对表头/年份/单位，不能只看列数
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from statistics import median

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
    bbox: Optional[list[float]] = None
    table_index: int = 0


@dataclass
class TableGrid:
    years: list[int] = field(default_factory=list)
    rows: list[TableRow] = field(default_factory=list)
    header_fingerprint: str = ""
    issues: list[str] = field(default_factory=list)

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
    unit_markers = [
        "吨",
        "兆瓦时",
        "立方米",
        "万元",
        "千元",
        "元",
        "千瓦时",
        "标万立方米",
        "升",
        "万升",
        "标准煤",
        "二氧化碳当量",
        "%",
        "CO2",
        "CO₂",
        "MWh",
        "m3",
        "m³",
        "tCO",
        "标万",
    ]
    return any(m in t for m in unit_markers)


@dataclass
class Footnote:
    number: str
    text: str
    bbox: list[float]
    words: list[Word] = field(default_factory=list, repr=False)


def footnote_references(text: str) -> list[str]:
    return list(
        dict.fromkeys(re.findall(r"(?<=[A-Za-z\u4e00-\u9fff）)])\s*(\d{1,3})(?=\s|$)", text))
    )


def _bbox(words):
    return [
        min(w.x0 for w in words),
        min(w.top for w in words),
        max(w.x1 for w in words),
        max(w.bottom for w in words),
    ]


def extract_footnotes(words: list[Word]) -> list[Footnote]:
    """脚注编号须在原文有引用，且定义编号使用小于正文的字号。"""
    references = {n for w in words for n in footnote_references(w.text)}
    lines = cluster_rows(words)
    starts = []
    for line in lines:
        for marker, following in zip(line, line[1:]):
            size = following.bottom - following.top
            if (
                marker.text in references
                and marker.text.isdigit()
                and not _looks_like_value(following.text)
                and 0 <= following.x0 - marker.x1 <= 2 * size
                and marker.bottom - marker.top < size * 0.95
            ):
                starts.append((marker, size))
    notes = []
    for marker, size in starts:
        end = min(
            (m.top for m, _ in starts if m.top > marker.top and abs(m.x0 - marker.x0) <= 2 * size),
            default=float("inf"),
        )
        right = min(
            (m.x0 for m, _ in starts if m.x0 > marker.x0 + 2 * size),
            default=float("inf"),
        )
        selected = []
        last_y = marker.ycenter
        for line in lines:
            part = [
                w
                for w in line
                if marker.top - size * 0.25 <= w.top < end
                and marker.x0 - size * 0.5 <= w.x0 < right
            ]
            if not part:
                continue
            y = min(w.ycenter for w in part)
            if y - last_y > size * 4 or any(w.bottom - w.top > size * 1.6 for w in part):
                break
            if not any(not _looks_like_value(w.text) for w in part):
                continue
            selected.extend(part)
            last_y = max(w.ycenter for w in part)
        if selected:
            notes.append(
                Footnote(marker.text, " ".join(w.text for w in selected), _bbox(selected), selected)
            )
    return notes


@dataclass
class _Header:
    label: Word
    unit: Optional[Word]
    years: list[Word]


def _headers(lines):
    found = []
    for line in lines:
        labels = [
            w for w in line if w.text.strip().lower() in {"指标", "indicator", "metric", "item"}
        ]
        for i, label in enumerate(labels):
            end = labels[i + 1].x0 if i + 1 < len(labels) else float("inf")
            part = [w for w in line if label.x0 < w.x0 < end]
            years = [w for w in part if _year_from_token(w.text) is not None]
            if years:
                unit = next((w for w in part if w.text.strip().lower() in {"单位", "unit"}), None)
                found.append(_Header(label, unit, years))
    return found


def _cell_text(words):
    text = ""
    for w in words:
        if text and re.search(r"[A-Za-z]$", text) and re.match(r"[A-Za-z]", w.text):
            text += " "
        text += w.text
    return text


def _cells(lines, numeric, size, units=None):
    cells = []
    for line in lines:
        if not line:
            continue
        y = median(w.ycenter for w in line)
        nearest = (
            min(range(len(numeric)), key=lambda i: abs(numeric[i]["y"] - y)) if numeric else None
        )
        join = bool(cells) and y - cells[-1]["last_y"] <= size * 2.3
        if join and nearest is not None and cells[-1]["nearest"] != nearest:
            prev = cells[-1]
            aligned = (
                abs(numeric[nearest]["y"] - y) <= size * 0.6
                and abs(numeric[prev["nearest"]]["y"] - prev["last_y"]) <= size * 0.6
            )
            # 不同单位的数值行可以共用纵向合并的指标格。
            shared = (
                units is not None
                and units[nearest]
                and units[prev["nearest"]]
                and units[nearest] != units[prev["nearest"]]
            )
            join = shared and not aligned
        if join:
            cells[-1]["words"].extend(line)
            cells[-1].update(last_y=y, nearest=nearest)
        else:
            cells.append(dict(words=list(line), last_y=y, nearest=nearest))
    for c in cells:
        c["text"] = _cell_text(c["words"])
        c["y"] = (min(w.ycenter for w in c["words"]) + max(w.ycenter for w in c["words"])) / 2
    return cells


def _closest(cells, y, size, limit):
    if not cells:
        return None, False
    ranked = sorted(cells, key=lambda c: abs(c["y"] - y))
    if abs(ranked[0]["y"] - y) > limit * size:
        return None, False
    ambiguous = (
        len(ranked) > 1
        and ranked[0]["text"] != ranked[1]["text"]
        and abs(abs(ranked[0]["y"] - y) - abs(ranked[1]["y"] - y)) < size * 0.4
    )
    return (None, True) if ambiguous else (ranked[0], False)


def _parse_block(header, words, size, index, tolerance):
    pairs = [(_year_from_token(w.text), w.xcenter) for w in header.years]
    grid = TableGrid(
        years=[y for y, _ in pairs],
        header_fingerprint="|".join(f"{y}@{round(x)}" for y, x in pairs),
    )
    if len(set(grid.years)) != len(grid.years):
        grid.issues.append("AMBIGUOUS_YEAR_HEADER")
        return grid
    numeric_min = ((header.unit.x1 if header.unit else header.label.x1) + header.years[0].x0) / 2
    centers = [x for _, x in pairs]
    gaps = [b - a for a, b in zip(centers, centers[1:])]
    radius = min(gaps) * 0.6 if gaps else max(size * 4, header.years[0].x1 - header.years[0].x0)
    labels_by_line = []
    units_by_line = []
    numeric = []
    for line in cluster_rows(words, tolerance):
        labels = []
        units = []
        values = {}
        duplicates = set()
        invalid = False
        count = 0
        for word in line:
            if word.xcenter >= numeric_min:
                if _looks_like_value(word.text):
                    year, x = min(pairs, key=lambda p: abs(p[1] - word.xcenter))
                    count += 1
                    if abs(x - word.xcenter) > radius:
                        grid.issues.append("UNBOUND_NUMERIC_CELL")
                    elif year in values:
                        duplicates.add(year)
                    else:
                        values[year] = word.text
                else:
                    invalid = True
            elif header.unit is not None and word.x0 >= header.unit.x0 - size * 0.5:
                units.append(word)
            elif header.unit is None and _looks_like_unit(word.text):
                units.append(word)
            else:
                labels.append(word)
        for year in duplicates:
            values.pop(year, None)
            grid.issues.append("DUPLICATE_YEAR_CELL")
        labels_by_line.append(labels)
        units_by_line.append(units)
        if count:
            numeric.append(
                dict(
                    y=median(w.ycenter for w in line),
                    values=values,
                    words=line,
                    unit_words=units,
                    count=count,
                )
            )
            if invalid:
                grid.issues.append("NON_NUMERIC_TABLE_CELL")
    unit_cells = _cells(units_by_line, numeric, size)
    assigned = [_closest(unit_cells, r["y"], size, 2.5)[0] for r in numeric]
    label_cells = _cells(
        labels_by_line, numeric, size, units=[c["text"] if c else None for c in assigned]
    )
    for row in numeric:
        label, ambiguous = _closest(label_cells, row["y"], size, 4)
        unit, bad_unit = _closest(unit_cells, row["y"], size, 2.5)
        if label is None:
            if row["count"] > 1 or row["unit_words"]:
                grid.issues.append("AMBIGUOUS_ROW_LABEL" if ambiguous else "ROW_LABEL_UNRESOLVED")
            continue
        if bad_unit or (header.unit and unit is None):
            grid.issues.append("AMBIGUOUS_ROW_UNIT" if bad_unit else "ROW_UNIT_UNRESOLVED")
        source = row["words"] + label["words"] + (unit["words"] if unit else [])
        grid.rows.append(
            TableRow(
                label["text"], unit["text"] if unit else None, row["values"], _bbox(source), index
            )
        )
    if not grid.rows:
        grid.rows = [TableRow(c["text"]) for c in label_cells]
    return grid


def reconstruct_table(words: list[Word], y_tolerance: float = 3.0) -> TableGrid:
    """按独立表头分栏/分块，再在块内还原，保持原单表返回接口。"""
    if not words:
        return TableGrid()
    size = median(w.bottom - w.top for w in words)
    lines = cluster_rows(words, y_tolerance)
    headers = _headers(lines)
    if not headers:
        return TableGrid(rows=[TableRow(" ".join(w.text for w in line)) for line in lines])
    lanes = []
    for h in sorted(headers, key=lambda h: h.label.x0):
        if not lanes or abs(lanes[-1][0].label.x0 - h.label.x0) > size * 2:
            lanes.append([h])
        else:
            lanes[-1].append(h)
    bounds = [
        (max(w.x1 for h in a for w in h.years) + min(h.label.x0 for h in b)) / 2
        for a, b in zip(lanes, lanes[1:])
    ]
    grids = []
    for j, lane in enumerate(lanes):
        left = bounds[j - 1] if j else -float("inf")
        right = bounds[j] if j < len(bounds) else float("inf")
        ordered = sorted(lane, key=lambda h: h.label.ycenter)
        for i, h in enumerate(ordered):
            end = ordered[i + 1].label.ycenter if i + 1 < len(ordered) else float("inf")
            part = [
                w
                for w in words
                if left <= w.xcenter < right
                and h.label.ycenter + y_tolerance < w.ycenter < end - y_tolerance
            ]
            grids.append(_parse_block(h, part, size, len(grids), y_tolerance))
    return TableGrid(
        years=list(dict.fromkeys(y for g in grids for y in g.years)),
        rows=[r for g in grids for r in g.rows],
        header_fingerprint=";".join(g.header_fingerprint for g in grids),
        issues=list(dict.fromkeys(c for g in grids for c in g.issues)),
    )


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


def detect_value_conflicts(grid: TableGrid) -> list[dict]:
    """检测同指标同年多处取值 → 冲突候选或互证（不静默拆散）。

    与用户提出的「两处取值都是信息」对齐：
    - 同 label + 同 year 出现多个**不同**值 → CROSS_REF_CONFLICT（需人工/业务判）
    - 同 label + 同 year 出现多个**相同**值 → CROSS_REF_AGREE（互证）
    """
    groups: dict[tuple[str, int], list[str]] = {}
    for row in grid.rows:
        if not row.label:
            continue
        for year, value in row.values.items():
            groups.setdefault((row.label, year), []).append(value)

    out: list[dict] = []
    for (label, year), values in groups.items():
        if len(values) < 2:
            continue
        distinct = sorted(set(values))
        out.append(
            {
                "code": "CROSS_REF_CONFLICT" if len(distinct) > 1 else "CROSS_REF_AGREE",
                "row_label": label,
                "year": year,
                "values": distinct,
                "occurrences": len(values),
            }
        )
    return out


def make_evidence_page_ref(page_index_0based: int, printed_page: Optional[str]) -> dict:
    """把解析器 0 基页索引转成 1 基 PDF 物理页；印刷页另存不混用。"""
    return {
        "pdf_page": page_index_0based + 1,
        "printed_page": printed_page,
    }
