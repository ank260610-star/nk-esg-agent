"""Presentation-only formatting; never derive scores or metric values here."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import json
from jinja2 import Undefined
from pathlib import Path
from urllib.parse import quote

STATUS_LABELS = {
    "extracted": "已提取",
    "unknown": "未知",
    "missing_marker": "原文明示缺失",
    "conflict": "数据冲突",
    "parse_error": "解析问题",
    "unreviewed": "待复核",
    "confirmed": "已人工确认",
    "rejected": "已拒绝",
    "met": "满足",
    "partial": "部分满足",
    "not_met": "不满足",
    "not_applicable": "不适用",
    "queued": "排队中",
    "running": "运行中",
    "waiting_review": "待人工复核",
    "completed": "本轮运行结束",
    "failed": "运行失败",
    "cancelled": "已取消",
    "needs_review": "可比性待复核",
    "not_comparable": "不可比",
    "comparable": "可比",
    "undefined": "无法计算",
    "insufficient_evidence": "证据不足，待复核",
    "no_applicable_items": "无适用项",
    "reference_score": "参考分",
    "final_score": "最终分",
}


def present(value):
    return (
        "待补齐"
        if isinstance(value, Undefined)
        else "未提供" if value is None else "空字符串" if value == "" else str(value)
    )


def percent(value):
    if value is None or value == "":
        return "未定"
    try:
        number = Decimal(str(value))
        return f"{number * 100:.2f}%" if number.is_finite() else "未定"
    except InvalidOperation:
        return "未定"


def evidence_view(evidence):
    """Ignore supplied viewer_url; construct same-origin links from bound evidence."""
    value = evidence.to_dict()
    rid, page = value.get("report_id", ""), value.get("pdf_page")
    value["viewer_url"] = None
    if (
        isinstance(rid, str)
        and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", rid)
        and type(page) is int
        and page > 0
    ):
        value["viewer_url"] = f'/api/reports/{quote(rid, safe="")}/pdf#page={page}'
    return value


METRIC_NAMES = {
    key: value["name"]
    for key, value in json.loads(
        (Path(__file__).parent / "rules/metric-dictionary.json").read_text(
            encoding="utf-8"
        )
    )["metrics"].items()
}


def points(value):
    if value is None:
        return "未定"
    try:
        number = Decimal(str(value))
        return (
            str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            if number.is_finite()
            else "未定"
        )
    except InvalidOperation:
        return "未定"
