"""Unrelated synthetic labels/years and scaled coordinates guard against PDF-specific fixes."""

import pytest
from esg_app.parsing import Word, reconstruct_table, extract_footnotes
from esg_app.parse_service import materialize
from esg_app.retrieval import Evidence, EvidenceStore, search_evidence


def w(text, x, y, width=35, height=10):
    return Word(text, x, x + width, y, y + height)


def header(x=0, y=0):
    return [
        w("指标", x, y),
        w("单位", x + 100, y),
        w("2019年", x + 220, y),
        w("2021年", x + 300, y),
    ]


@pytest.mark.parametrize("scale,shift", [(1, 0), (1.7, 23)])
def test_parallel_tables(scale, shift):
    words = (
        header()
        + header(430)
        + [
            w("蒸汽用量", 0, 40),
            w("MWh", 100, 40),
            w("11.00", 220, 40),
            w("12.00", 300, 40),
            w("总耗水量", 430, 40),
            w("立方米", 530, 40),
            w("31.00", 650, 40),
            w("32.00", 730, 40),
        ]
    )
    words = [
        Word(p.text, p.x0 * scale + shift, p.x1 * scale + shift, p.top * scale, p.bottom * scale)
        for p in words
    ]
    rows = {r.label: r for r in reconstruct_table(words).rows if r.values}
    assert rows["蒸汽用量"].values == {2019: "11.00", 2021: "12.00"}
    assert rows["总耗水量"].values == {2019: "31.00", 2021: "32.00"}
    assert rows["蒸汽用量"].unit == "MWh"
    assert rows["总耗水量"].unit == "立方米"


def test_headers_after_intro_and_repeated_blocks():
    words = (
        [w("intro paragraph", 0, i * 20) for i in range(8)]
        + header(y=200)
        + [w("第一指标", 0, 230), w("立方米", 100, 230), w("17", 220, 230), w("19", 300, 230)]
    )
    words += header(y=280) + [
        w("第二指标", 0, 310),
        w("兆瓦时", 100, 310),
        w("23", 220, 310),
        w("29", 300, 310),
    ]
    rows = {r.label: r for r in reconstruct_table(words).rows if r.values}
    assert rows["第一指标"].values[2021] == "19"
    assert rows["第二指标"].values[2019] == "23"


def test_split_labels_units_and_missing_marker():
    words = header() + [
        w("排放密度", 0, 30),
        w("（运营范围）", 0, 50),
        w("吨CO2e/", 100, 30),
        w("万元营收", 100, 50),
        w("/", 220, 40),
        w("0.1800", 300, 40),
    ]
    rows = [r for r in reconstruct_table(words).rows if r.values]
    assert len(rows) == 1
    assert rows[0].label == "排放密度（运营范围）"
    assert rows[0].unit == "吨CO2e/万元营收"
    assert rows[0].values == {2019: "/", 2021: "0.1800"}


def test_shared_label_for_distinct_unit_rows():
    rows = reconstruct_table(
        header()
        + [
            w("蒸汽用量", 0, 50),
            w("兆瓦时", 100, 40),
            w("11", 220, 40),
            w("13", 300, 40),
            w("吨标准煤", 100, 65),
            w("17", 220, 65),
            w("19", 300, 65),
        ]
    ).rows
    assert len(rows) == 2
    assert all(r.label == "蒸汽用量" for r in rows)
    assert [r.unit for r in rows] == ["兆瓦时", "吨标准煤"]


def test_ambiguous_cells_are_reported():
    grid = reconstruct_table(
        header()
        + [
            w("第一指标", 0, 40),
            w("立方米", 100, 40),
            w("11", 220, 40, 15),
            w("13", 245, 40, 15),
            w("17", 300, 40),
        ]
    )
    assert "DUPLICATE_YEAR_CELL" in grid.issues
    assert 2019 not in grid.rows[0].values
    orphan = reconstruct_table(header() + [w("吨", 100, 150), w("11", 220, 150), w("13", 300, 150)])
    assert "ROW_LABEL_UNRESOLVED" in orphan.issues
    assert not any(r.values for r in orphan.rows)


def test_numbered_footnote_and_bidirectional_links():
    words = (
        header()
        + header(430)
        + [
            w("其他指标", 0, 120),
            w("立方米", 100, 120),
            w("11", 220, 120),
            w("13", 300, 120),
            w("间接排放量7", 430, 40),
            w("吨", 530, 40),
            w("/", 650, 40),
            w("23", 730, 40),
            w("7", 430, 115, 4, 5),
            w("核算包含第三方运输。", 438, 117, 95, 7),
            w("采用独立排放因子。", 430, 129, 95, 7),
        ]
    )
    notes = extract_footnotes(words)
    assert len(notes) == 1 and notes[0].number == "7"
    assert "其他指标" not in notes[0].text
    assert "独立排放因子" in notes[0].text
    parsed = {
        "page_count": 1,
        "pages": [
            {
                "number": 1,
                "text": "原页全文",
                "rows": [
                    {
                        "label": "间接排放量7",
                        "unit": "吨",
                        "values": {2019: "/", 2021: "23"},
                        "footnote_refs": ["7"],
                    }
                ],
                "issue": None,
                "footnotes": [{"number": n.number, "text": n.text, "bbox": n.bbox} for n in notes],
            }
        ],
    }
    _, evidence = materialize("r", "hash", "v", parsed)
    note = next(e for e in evidence if e["kind"] == "footnote")
    tables = [e for e in evidence if e["kind"] == "table"]
    assert all(note["evidence_id"] in e["linked_evidence_ids"] for e in tables)
    assert set(note["linked_evidence_ids"]) == {e["evidence_id"] for e in tables}


def test_explicit_link_and_no_cross_report_footnote():
    body = Evidence("e", "r", 4, None, "table", "范围3温室气体排放量", linked_evidence_ids=["note"])
    note = Evidence(
        "note", "r", 4, None, "footnote", "核算包含第三方运输。", linked_evidence_ids=["e"]
    )
    foreign = Evidence("foreign", "other", 4, None, "footnote", "范围3温室气体排放量包括员工通勤")
    res = search_evidence(
        EvidenceStore([body, note, foreign]), "范围3温室气体排放", metric_id="E03", limit=1
    )
    assert {e.evidence_id for e in res["evidence"]} == {"e", "note"}


def test_augmented_results_respect_schema_limit():
    refs = [f"n{i}" for i in range(30)]
    body = Evidence("e", "r", 1, None, "table", "范围3温室气体排放量", linked_evidence_ids=refs)
    notes = [Evidence(n, "r", 1, None, "footnote", "核算说明") for n in refs]
    res = search_evidence(
        EvidenceStore([body, *notes]), "范围3温室气体排放", metric_id="E03", limit=1
    )
    assert len(res["evidence"]) == 20


def test_repeated_note_numbers_do_not_choose_one_definition():
    page = {
        "number": 1,
        "text": "原文",
        "issue": None,
        "rows": [
            {"label": "间接排放量7", "unit": "吨", "values": {2021: "23"}, "footnote_refs": ["7"]}
        ],
        "footnotes": [
            {"number": "7", "text": "7 第一项说明"},
            {"number": "7", "text": "7 另一栏说明"},
        ],
    }
    summary, evidence = materialize("r", "hash", "v", {"page_count": 1, "pages": [page]})
    assert summary["status"] == "partial"
    assert any(i["code"] == "AMBIGUOUS_FOOTNOTE_REFERENCE" for i in summary["issues"])
    assert len([e for e in evidence if e["kind"] == "footnote"]) == 2
    assert not next(e for e in evidence if e["kind"] == "table")["linked_evidence_ids"]


def test_binding_issues_keep_original_text_and_partial_status():
    parsed = {
        "page_count": 1,
        "pages": [
            {
                "number": 1,
                "text": "unbound original 1,234",
                "rows": [],
                "issue": None,
                "issues": ["ROW_LABEL_UNRESOLVED", "ROW_LABEL_UNRESOLVED"],
            }
        ],
    }
    summary, evidence = materialize("r", "hash", "v", parsed)
    assert summary["status"] == "partial"
    assert len(summary["issues"]) == 1
    assert evidence[0]["text"] == "unbound original 1,234"


def test_two_equidistant_labels_are_not_guessed():
    grid = reconstruct_table(
        header()
        + [
            w("第一指标", 0, 30),
            w("第二指标", 0, 70),
            w("立方米", 100, 50),
            w("11", 220, 50),
            w("13", 300, 50),
        ]
    )
    assert "AMBIGUOUS_ROW_LABEL" in grid.issues
    assert not any(r.values for r in grid.rows)


def test_api_rejects_limit_over_contract_maximum(tmp_path):
    from fastapi.testclient import TestClient
    from esg_app.main import create_app
    from esg_app.config import Settings

    app = create_app(Settings(database_path=str(tmp_path / "db.sqlite3")))
    app.state.tasks.register_task("t", 0, {"report_ids": []})
    app.state.tasks.issue_agent_token("t", "test-token")
    response = TestClient(app).post(
        "/agent/tool",
        headers={"X-Agent-Token": "test-token"},
        json={"tool": "search_evidence", "arguments": {"query": "anything", "limit": 21}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_parallel_footnote_columns_do_not_merge():
    words = [
        w("排放量7", 0, 20),
        w("耗水量8", 430, 20),
        w("7", 0, 100, 4, 5),
        w("左栏核算说明", 8, 102, 95, 7),
        w("8", 430, 100, 4, 5),
        w("右栏核算说明", 438, 102, 95, 7),
    ]
    notes = {n.number: n.text for n in extract_footnotes(words)}
    assert "左栏" in notes["7"] and "右栏" not in notes["7"]
    assert "右栏" in notes["8"] and "左栏" not in notes["8"]


def test_full_body_budget_still_returns_referenced_footnote():
    body = [
        Evidence(f"e{i}", "r", 1, None, "table", "范围3温室气体排放量", linked_evidence_ids=["n"])
        for i in range(20)
    ]
    note = Evidence("n", "r", 1, None, "footnote", "核算说明")
    res = search_evidence(
        EvidenceStore([*body, note]), "范围3温室气体排放", metric_id="E03", limit=20
    )
    assert len(res["evidence"]) == 20
    assert res["evidence"][0].evidence_id == "e0"
    assert note in res["evidence"]
    assert any(i["code"] == "EVIDENCE_RESULT_LIMIT" for i in res["issues"])
