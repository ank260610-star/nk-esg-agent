"""复合单位别名归一：基于 E02 实机证据的单位分支回归。

单位字面值取自 E02-evidence-handoff-20261125 证据包中保留的
raw_unit / canonical_unit；数值与证据文本取自该包引用的原表证据，
仅作显式测试输入，不代表恢复出的原始模型候选。
"""

import pytest

from esg_app.model_extractor import grounded, unit_identity


RAW = "吨二氧化碳当量/万元营收"
CANONICAL = "tCO2e/万元营收"
EVIDENCE_TEXT = "温室气体排放密度（范围1+范围2） 2023 0.2049 吨二氧化碳当量/万元营收"


@pytest.mark.parametrize(
    "left,right",
    [
        # 证据包两年度实际保留的组合，及反向写法
        (RAW, CANONICAL),
        (CANONICAL, RAW),
        # 既有简单单位别名行为不得回退
        ("吨二氧化碳当量", "tCO2e"),
        ("兆瓦时", "MWh"),
        ("千瓦时", "kWh"),
        ("立方米", "m3"),
        # 分母规范化后相同仍应等价
        ("吨二氧化碳当量 / 万元营收", "tCO2e/万元营收"),
    ],
)
def test_equivalent_units_are_identified(left, right):
    assert unit_identity(left) == unit_identity(right)


@pytest.mark.parametrize(
    "left,right",
    [
        # 分母含义不同：万元营收 ≠ 亿元营收
        (RAW, "tCO2e/亿元营收"),
        (RAW, "tCO2e/万元产值"),
        ("吨二氧化碳当量/万元营收", "tCO2e/万元营收/年"),
        # 数量级不同不得靠别名替换抹平
        ("万吨二氧化碳当量/万元营收", CANONICAL),
        ("kgCO2e/万元营收", CANONICAL),
        # 分子不同
        ("MWh/万元营收", CANONICAL),
        # 缺分母不能等价于带分母
        (CANONICAL, "tCO2e"),
        (RAW, "吨二氧化碳当量"),
    ],
)
def test_non_equivalent_units_are_rejected(left, right):
    assert unit_identity(left) != unit_identity(right)


def test_grounded_accepts_cited_compound_unit_with_alias():
    """实机 E02 单位组合 + 引用文本应通过 grounded（显式测试输入）。"""
    candidate = {
        "raw_value": "0.2049",
        "value": "0.2049",
        "raw_unit": RAW,
        "canonical_unit": CANONICAL,
        "evidence_ids": ["ev1"],
    }
    assert grounded(candidate, {"ev1": {"text": EVIDENCE_TEXT}}) is True


def test_grounded_rejects_missing_unit_context():
    """单位字面串未出现在引用文本时仍须拒绝（缺证反例）。"""
    candidate = {
        "raw_value": "0.2049",
        "value": "0.2049",
        "raw_unit": RAW,
        "canonical_unit": CANONICAL,
        "evidence_ids": ["ev1"],
    }
    assert grounded(candidate, {"ev1": {"text": "温室气体排放密度 2023 0.2049"}}) is False


def test_grounded_rejects_wrong_denominator():
    candidate = {
        "raw_value": "0.2049",
        "value": "0.2049",
        "raw_unit": RAW,
        "canonical_unit": "tCO2e/亿元营收",
        "evidence_ids": ["ev1"],
    }
    assert grounded(candidate, {"ev1": {"text": EVIDENCE_TEXT}}) is False
