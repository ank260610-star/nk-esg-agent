"""数值证据边界反例：经 HTTP 接口验证拒绝后无 revision/记录/审计/幂等残留（额度 0）。

对应用户交接文件第 5 节「先修复剩余的数值证据漏洞」的全部用例，
以及原有 canonical_unit／错误 raw_value／非法缺失标记反例的回归。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from esg_app.retrieval import Evidence, EvidenceStore  # noqa: E402
from test_metric_review import (  # noqa: E402
    correction_body, review, seed, setup, state, unknown_record,
)


def setup_text(tmp_path, text, year=2024):
    app, c = setup(tmp_path)
    app.state.evidence = EvidenceStore([
        Evidence("evN", "r1", 127, None, "table", text, None, None, None, year, []),
    ])
    return app, c


def assert_no_side_effects(app):
    assert app.state.tasks.get("t1").revision == 3
    assert not state(app).get("metric_review_events", [])
    assert len(state(app)["records"]) == 1
    with app.state.tasks.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0] == 0


@pytest.mark.parametrize("text,value,raw_value", [
    (".1 tCO2e/万元营收", "1", "1"),                       # .1 不得拆出 1
    ("0.1300 tCO2e/万元营收", "1300", "1300"),              # 0.1300 不得拆出 1300
    ("1.23e4 tCO2e/万元营收", "1.23", "1.23"),              # 科学计数法局部
    ("1.23e4 tCO2e/万元营收", "4", "4"),                    # 科学计数法尾数
    ("2024\n100 tCO2e/万元营收", "2024100", "2024100"),      # 换行不得拼接
    ("100 万tCO2e/万元营收", "100", "100"),                  # 数量级误配
])
def test_partial_or_concatenated_numbers_rejected(tmp_path, text, value, raw_value):
    app, c = setup_text(tmp_path, text)
    seed(app, unknown_record(), artifacts=0)
    body = correction_body(evidence_ids=["evN"])
    body["correction"]["value"] = value
    body["correction"]["raw_value"] = raw_value
    body["correction"]["raw_unit"] = "tCO2e/万元营收"
    body["correction"]["canonical_unit"] = "tCO2e/万元营收"
    response = review(c, body)
    assert response.status_code == 400, response.text
    assert "evidence does not support" in response.json()["error"]["message"]
    assert_no_side_effects(app)


@pytest.mark.parametrize("text,value,raw_value", [
    ("1,000.00 tCO2e/万元营收", "1000", "1,000.00"),        # 千分位等值
    ("0.1300 tCO2e/万元营收", "0.13", "0.1300"),            # 尾随零等值
])
def test_equivalent_numbers_accepted_with_original_strings(tmp_path, text, value, raw_value):
    app, c = setup_text(tmp_path, text)
    seed(app, unknown_record(), artifacts=0)
    body = correction_body(evidence_ids=["evN"])
    body["correction"]["value"] = value
    body["correction"]["raw_value"] = raw_value
    body["correction"]["raw_unit"] = "tCO2e/万元营收"
    body["correction"]["canonical_unit"] = "tCO2e/万元营收"
    response = review(c, body)
    assert response.status_code == 200, response.text
    record = response.json()["data"]["record"]
    assert record["value"] == value          # 标准值保留请求字符串
    assert record["raw_value"] == raw_value  # 原文值保留请求字符串
    assert app.state.tasks.get("t1").revision == 4
    assert len(state(app).get("metric_review_events", [])) == 1


def test_unit_slash_is_not_a_missing_marker(tmp_path):
    app, c = setup_text(tmp_path, "tCO2e/万元营收")
    seed(app, unknown_record(), artifacts=0)
    body = correction_body(evidence_ids=["evN"])
    body["correction"] = {
        "status": "missing_marker", "raw_value": "/", "value": None,
        "raw_unit": None, "canonical_unit": None, "conversion_rule": None,
        "denominator": None, "extraction_note": "单位中的斜杠不是缺失标记",
    }
    response = review(c, body)
    assert response.status_code == 400, response.text
    assert_no_side_effects(app)


def test_arbitrary_canonical_unit_still_rejected(tmp_path):
    app, c = setup_text(tmp_path, "0.1300 tCO2e/万元营收")
    seed(app, unknown_record(), artifacts=0)
    body = correction_body(evidence_ids=["evN"])
    body["correction"]["canonical_unit"] = "arbitrary-unit"
    body["correction"]["conversion_rule"] = "任意换算说明"
    response = review(c, body)
    assert response.status_code == 400, response.text
    assert_no_side_effects(app)


def test_wrong_raw_value_still_rejected(tmp_path):
    app, c = setup_text(tmp_path, "0.1300 tCO2e/万元营收")
    seed(app, unknown_record(), artifacts=0)
    body = correction_body(evidence_ids=["evN"])
    body["correction"]["value"] = "0.9999"
    body["correction"]["raw_value"] = "0.9999"
    response = review(c, body)
    assert response.status_code == 400, response.text
    assert_no_side_effects(app)


def test_plain_text_missing_marker_still_rejected(tmp_path):
    app, c = setup_text(tmp_path, "详见附录说明")
    seed(app, unknown_record(), artifacts=0)
    body = correction_body(evidence_ids=["evN"])
    body["correction"] = {
        "status": "missing_marker", "raw_value": "未披露", "value": None,
        "raw_unit": None, "canonical_unit": None, "conversion_rule": None,
        "denominator": None, "extraction_note": "正文无独立缺失标记",
    }
    response = review(c, body)
    assert response.status_code == 400, response.text
    assert_no_side_effects(app)
