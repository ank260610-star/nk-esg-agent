"""指标确认/修正接口 v0.2：归属、版本、审计、失效、幂等、事务（离线，额度 0）。"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.config import Settings
from esg_app.main import create_app
from esg_app.parse_repository import read_business
from esg_app.retrieval import Evidence, EvidenceStore
from test_comparison import rec

USER = {"X-User": "local_user"}


def setup(tmp_path, owner=None):
    app = create_app(Settings(database_path=str(tmp_path / "test.db")))
    task = {"task_id": "t1", "report_ids": ["r1"], "selected_years": [2023, 2024]}
    if owner:
        task["owner"] = owner
    app.state.tasks.register_task(
        "t1", 3,
        {"task": task, "report_ids": ["r1"], "selected_years": [2023, 2024]},
    )
    app.state.tasks.issue_agent_token("t1", "token")
    app.state.evidence = EvidenceStore([
        Evidence("ev1", "r1", 127, None, "table", "0.1300 tCO2e/万元营收",
                 None, None, None, 2024, []),
        Evidence("ev2", "r1", 42, None, "text", "0.1200 tCO2e/万元营收",
                 None, None, None, 2023, []),
        Evidence("ev3", "r2", 42, None, "text", "0.9999",
                 None, None, None, 2024, []),
        Evidence("ev4", "r1", 127, None, "table", "/ 未披露",
                 None, None, None, 2023, []),
    ])
    return app, TestClient(app)


def unknown_record(year=2024, evidence_id="ev1"):
    record = rec(
        metric_id="E02", year=year, value="0.1200",
        unit="tCO2e/万元营收", report_id="r1", task_id="t1",
        evidence_ids=[evidence_id], status="unknown",
        extraction_note="附录口径待人工核对",
    )
    record["value"] = None  # unknown 必须无数值
    return record


def seed(app, record, artifacts=2):
    with app.state.tasks.transaction() as db:
        state = read_business(db, "t1")
        state["records"] = [record]
        state["checks"] = [{
            "check_id": "Q02", "rule_version": "disclosure-v0.1", "status": "met",
            "evidence_ids": [], "reason": "现有检查", "missing_information": [],
            "suggestion": "", "target_key": None, "task_id": "t1",
            "origin": "model", "review_state": "unreviewed", "revision": 3,
            "is_current": True,
        }]
        state["comparisons"] = [{
            "comparison_id": "cmp1", "older_record_id": record["record_id"],
            "newer_record_id": "other", "status": "comparable", "reason_codes": [],
            "delta": "1", "percent_change": "1.00", "unit": "tCO2e/万元营收",
            "evidence_ids": [], "revision": 3, "is_current": True,
        }]
        state["score"] = {"revision": 3, "is_current": True, "label": "final"}
        from esg_app.parse_repository import save_business
        save_business(db, "t1", state)
    ids = []
    for i in range(artifacts):
        meta = app.state.artifacts.add(
            "t1", "json", "application/json", f"a{i}.json", b"{}", 3
        )
        ids.append(meta["artifact_id"])
    return ids


def review(c, body=None, key=None, headers=None):
    payload = body if body is not None else {
        "action": "confirm",
        "record_id": "r_E02_2024",
        "expected_revision": 3,
        "reason": "对照 p127 附录原表确认口径",
        "evidence_ids": ["ev1"],
    }
    return c.post(
        "/tasks/t1/metric-reviews", json=payload,
        headers={**USER, "Idempotency-Key": key or str(uuid4()), **(headers or {})},
    )


def state(app):
    with app.state.tasks.transaction() as db:
        return read_business(db, "t1")


def correction_body(**overrides):
    body = {
        "action": "correct",
        "record_id": "r_E02_2024",
        "expected_revision": 3,
        "reason": "原记录为 unknown，按 p127 附录原表补正",
        "evidence_ids": ["ev1"],
        "correction": {
            "status": "extracted",
            "value": "0.1300",
            "raw_value": "0.1300",
            "raw_unit": "吨二氧化碳当量/万元营收",
            "canonical_unit": "tCO2e/万元营收",
            "conversion_rule": None,
            "denominator": {
                "kind": "revenue", "currency": "CNY", "scale": "1", "unit": "万元营收",
            },
            "boundary_text": "范围1+2，附录原表口径",
            "boundary_key": "scope1_2",
            "method_text": "附录披露值，未换算",
        },
    }
    body.update(overrides)
    return body


def test_confirm_preserves_origin_status_and_invalidates(tmp_path):
    app, c = setup(tmp_path)
    record = unknown_record()
    artifacts = seed(app, record)
    response = review(c)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert response.json()["revision"] == 4
    current = [r for r in state(app)["records"] if r.get("is_current")][0]
    assert current["record_id"] == record["record_id"]
    assert current["origin"] == record["origin"]
    assert current["status"] == "unknown"  # confirm 不把无数值内容改成 extracted
    assert current["review_state"] == "confirmed"
    assert data["invalidated"]["checks"] == ["Q02"]
    assert data["invalidated"]["comparisons"] == ["cmp1"]
    assert data["invalidated"]["score"] == "stale"
    assert data["invalidated"]["exports"] == artifacts
    assert app.state.tasks.get("t1").revision == 4


def test_correct_unknown_to_extracted_keeps_original_and_audit(tmp_path):
    app, c = setup(tmp_path)
    old = unknown_record()
    seed(app, old, artifacts=0)
    response = review(c, correction_body())
    assert response.status_code == 200, response.text
    payload = response.json()
    new = payload["data"]["record"]
    assert new["record_id"] != old["record_id"]
    assert new["origin"] == "manual"
    assert new["supersedes_id"] == old["record_id"]
    assert new["derivation"] is None
    assert new["status"] == "extracted"
    assert new["value"] == "0.1300"
    assert new["review_state"] == "confirmed"
    assert payload["revision"] == 4
    records = {r["record_id"]: r for r in state(app)["records"]}
    assert records[old["record_id"]]["is_current"] is False
    assert records[old["record_id"]]["origin"] == old["origin"]  # 原模型记录不覆盖
    assert records[new["record_id"]]["is_current"] is True
    events = state(app).get("metric_review_events", [])
    assert len(events) == 1
    assert events[0]["audit_event_id"] == payload["data"]["audit_event_id"]
    assert events[0]["actor"] == "local_user"
    assert events[0]["before"]["record_id"] == old["record_id"]
    assert events[0]["after"]["value"] == "0.1300"


def test_correct_missing_marker_keeps_null_value(tmp_path):
    app, c = setup(tmp_path)
    old = unknown_record(year=2023, evidence_id="ev4")
    seed(app, old, artifacts=0)
    body = correction_body()
    body["record_id"] = old["record_id"]
    body["evidence_ids"] = ["ev4"]
    body["correction"] = {
        "status": "missing_marker", "raw_value": "/", "value": None,
        "canonical_unit": None, "conversion_rule": None, "denominator": None,
        "extraction_note": "原表单元格为 /，无可用数值",
    }
    response = review(c, body)
    assert response.status_code == 200, response.text
    new = response.json()["data"]["record"]
    assert new["status"] == "missing_marker"
    assert new["value"] is None
    assert new["raw_value"] == "/"
    assert new["origin"] == "manual"


def test_evidence_foreign_year_and_support_are_rejected(tmp_path):
    app, c = setup(tmp_path)
    seed(app, unknown_record(), artifacts=0)
    foreign = correction_body(evidence_ids=["ev3"])
    assert review(c, foreign).json()["error"]["code"] == "FOREIGN_EVIDENCE"
    wrong_year = correction_body(evidence_ids=["ev2"])  # ev2 属于 2023
    assert review(c, wrong_year).json()["error"]["code"] == "FOREIGN_EVIDENCE"
    unsupported = correction_body()
    unsupported["correction"]["value"] = "9.9999"
    response = review(c, unsupported)
    assert response.status_code == 400
    assert "evidence does not support" in response.json()["error"]["message"]
    assert app.state.tasks.get("t1").revision == 3
    assert len(state(app).get("metric_review_events", [])) == 0


def test_auth_agent_and_task_owner_are_enforced(tmp_path):
    app, c = setup(tmp_path, owner="alice")
    seed(app, unknown_record(), artifacts=0)
    assert review(c, headers={"X-User": ""}).status_code in (400, 401)
    assert review(c, headers={"Authorization": "Bearer token"}).status_code == 403
    c.get("/")  # 建立 esg_user=local_user Cookie，Agent 令牌不得借道
    assert review(c, headers={"Authorization": "Bearer token"}).status_code == 403
    assert review(c, headers={"X-User": "bob"}).status_code == 403
    assert app.state.tasks.get("t1").revision == 3


def test_revision_conflict_replay_and_key_conflict(tmp_path):
    app, c = setup(tmp_path)
    seed(app, unknown_record(), artifacts=0)
    stale = review(c, {
        "action": "confirm", "record_id": "r_E02_2024", "expected_revision": 2,
        "reason": "旧版本", "evidence_ids": ["ev1"],
    })
    assert stale.status_code == 409
    assert stale.json()["revision"] == 3
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

    key = str(uuid4())
    first = review(c, key=key)
    assert first.status_code == 200, first.text
    replay = review(c, key=key)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    different = review(c, {**correction_body(), "expected_revision": 4}, key=key)
    assert different.status_code == 409
    assert different.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert app.state.tasks.get("t1").revision == 4


def test_replay_survives_restart_without_second_write(tmp_path):
    app, c = setup(tmp_path)
    seed(app, unknown_record(), artifacts=0)
    key = str(uuid4())
    first = review(c, key=key)
    restarted = create_app(Settings(database_path=str(tmp_path / "test.db")))
    second = review(TestClient(restarted), key=key)
    assert second.json() == first.json()
    assert len(state(restarted).get("metric_review_events", [])) == 1
    assert restarted.state.tasks.get("t1").revision == 4


def test_concurrent_same_key_commits_once(tmp_path):
    app, c = setup(tmp_path)
    seed(app, unknown_record(), artifacts=0)
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(review, c, key=key).result() for _ in range(2)]
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert results[0].json() == results[1].json()
    assert app.state.tasks.get("t1").revision == 4
    assert len(state(app).get("metric_review_events", [])) == 1


@pytest.mark.parametrize("change", [
    {"actor": "fake"},
    {"action": "unknown"},
    {"expected_revision": True},
    {"expected_revision": "3"},
    {"evidence_ids": []},
    {"reason": "   "},
    {"correction": {}},
])
def test_strict_request_fields_are_rejected(tmp_path, change):
    app, c = setup(tmp_path)
    seed(app, unknown_record(), artifacts=0)
    body = {
        "action": "confirm", "record_id": "r_E02_2024", "expected_revision": 3,
        "reason": "核对", "evidence_ids": ["ev1"], **change,
    }
    assert review(c, body).status_code == 400
    assert app.state.tasks.get("t1").revision == 3
    assert not state(app).get("metric_review_events", [])


def test_foreign_record_is_not_found(tmp_path):
    app, c = setup(tmp_path)
    seed(app, unknown_record(), artifacts=0)
    response = review(c, {
        "action": "confirm", "record_id": "foreign_record", "expected_revision": 3,
        "reason": "外任务记录", "evidence_ids": ["ev1"],
    })
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert app.state.tasks.get("t1").revision == 3


def test_correction_rejects_server_fields_and_metric_year(tmp_path):
    app, c = setup(tmp_path)
    seed(app, unknown_record(), artifacts=0)
    for field in ("metric_year", "origin", "review_state", "actor", "is_current"):
        body = correction_body()
        body["correction"][field] = 2025 if field == "metric_year" else "fake"
        assert review(c, body).status_code == 400
    assert app.state.tasks.get("t1").revision == 3


def test_stale_metadata_cannot_survive_value_change(tmp_path):
    app, c = setup(tmp_path)
    old = unknown_record()
    old["conversion_rule"] = "旧换算：×1000"
    old["denominator"] = {
        "kind": "revenue", "currency": "CNY", "scale": "1", "unit": "万元营收",
    }
    seed(app, old, artifacts=0)
    body = correction_body()
    body["correction"].pop("conversion_rule")
    body["correction"].pop("denominator")
    response = review(c, body)
    assert response.status_code == 400
    assert "conversion_rule and denominator must be restated" in response.json()["error"]["message"]
    assert app.state.tasks.get("t1").revision == 3


def test_transaction_failure_leaves_no_partial_state(tmp_path, monkeypatch):
    import esg_app.metric_review as module

    app, c = setup(tmp_path)
    seed(app, unknown_record(), artifacts=0)
    def boom(*args, **kwargs):
        raise OSError("offline injected persistence failure")
    monkeypatch.setattr(module, "save_business", boom)
    response = review(c)
    assert response.status_code == 500
    assert app.state.tasks.get("t1").revision == 3
    assert not state(app).get("metric_review_events", [])
    with app.state.tasks.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0] == 0


def test_audit_read_is_owner_protected(tmp_path):
    app, c = setup(tmp_path, owner="alice")
    seed(app, unknown_record(), artifacts=0)
    assert review(c, headers={"X-User": "alice"}).status_code == 200
    url = "/tasks/t1/metric-reviews"
    denied = c.get(url, headers={"Authorization": "Bearer token"})
    assert denied.status_code == 403
    owned = c.get(url, headers={"X-User": "alice"}).json()
    event = owned["data"]["events"][0]
    assert event["actor"] == "alice"
    assert event["action"] == "confirm"
    assert event["reason"] and event["before"] and event["after"]
    assert c.get(url, headers={"X-User": "bob"}).json()["data"]["events"] == []
