"""HTTP writes against real SQLite, including races and process restart."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path
from uuid import uuid4
import json

import pytest
from fastapi.testclient import TestClient
from esg_app.main import create_app
from esg_app.config import Settings
from esg_app.extraction import ExtractionDeps
from esg_app.parse_repository import read_business
from esg_app.retrieval import Evidence, EvidenceStore
from esg_app.write_service import validate
from test_metrics import candidate
from test_comparison import rec


def setup(
    tmp_path, model=lambda ctx: [candidate(report_id="r1", evidence_ids=["ev1"])]
):
    app = create_app(Settings(database_path=str(tmp_path / "test.db")))
    app.state.tasks.register_task(
        "t1", 3, {"report_ids": ["r1"], "selected_years": [2023, 2024]}
    )
    app.state.tasks.issue_agent_token("t1", "token")
    app.state.extraction_deps = ExtractionDeps(model, "v1", "stub", "v1")
    app.state.evidence = EvidenceStore(
        [Evidence("ev1", "r1", 1, None, "text", "100 tco2e")]
    )
    return app, TestClient(app)


def post(c, route="extract", args=None, key=None, token="token"):
    return c.post(
        "/api/tasks/t1/" + route,
        json=(
            args
            if args is not None
            else {"expected_revision": 3, "metric_ids": ["E01"], "years": [2024]}
        ),
        headers={
            "Authorization": "Bearer " + token,
            "Idempotency-Key": key or str(uuid4()),
        },
    )


def state(app):
    with app.state.tasks.transaction() as db:
        return read_business(db, "t1")


def test_schema_package_matches_canonical():
    root = Path(__file__).resolve().parents[1]
    assert (root / "contracts/schema.json").read_bytes() == (
        root / "backend/src/esg_app/contract_schema.json"
    ).read_bytes()


def test_replay_is_exact_and_survives_restart(tmp_path):
    calls = []
    app, c = setup(
        tmp_path,
        lambda ctx: calls.append(1)
        or [candidate(report_id="r1", evidence_ids=["ev1"])],
    )
    key = str(uuid4())
    first = post(c, key=key)
    assert first.status_code == 200, first.text
    assert validate("extract_metrics_output", first.json())
    assert first.json()["revision"] == 4
    assert first.json() == post(c, key=key).json()
    restarted = create_app(Settings(database_path=str(tmp_path / "test.db")))
    assert first.json() == post(TestClient(restarted), key=key).json()
    assert len(calls) == 1
    assert len(restarted.state.records.list_by_task("t1")) == 1
    assert len(state(restarted)["runs"]) == 1
    assert (
        post(
            c,
            key=key,
            args={"expected_revision": 3, "metric_ids": ["E02"], "years": [2024]},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "change",
    [
        {"expected_revision": True},
        {"metric_ids": ["BAD"]},
        {"years": [True]},
        {"years": [2022]},
        {"metric_ids": ["E01", "E01"]},
        {"model_id": "injected"},
    ],
)
def test_strict_inputs_no_model_call(tmp_path, change):
    calls = []
    app, c = setup(tmp_path, lambda ctx: calls.append(1) or [])
    args = {"expected_revision": 3, "metric_ids": ["E01"], "years": [2024], **change}
    assert post(c, args=args).status_code == 422
    assert not calls
    assert app.state.tasks.get("t1").revision == 3


def test_same_key_in_flight_does_not_repeat_model(tmp_path):
    started, release = Event(), Event()
    calls = []

    def model(ctx):
        calls.append(1)
        started.set()
        assert release.wait(10)
        return []

    app, c = setup(tmp_path, model)
    key = str(uuid4())
    with ThreadPoolExecutor() as pool:
        first = pool.submit(post, c, key=key)
        assert started.wait(5)
        try:
            second = post(c, key=key)
            assert second.status_code == 422
            assert second.json()["error"]["code"] == "INVALID_STATE"
        finally:
            release.set()
        result = first.result()
    assert result.status_code == 200, result.text
    assert len(calls) == 1
    assert result.json()["data"]["status"] == "partial"
    assert result.json()["data"]["records"][0]["value"] is None


def test_revision_changed_during_model_has_no_candidate_writes_but_usage(tmp_path):
    app, c = setup(tmp_path)

    def model(ctx):
        app.state.tasks.get("t1").revision = 4
        return [candidate(report_id="r1", evidence_ids=["ev1"])]

    app.state.extraction_deps.model_fn = model
    response = post(c)
    assert response.status_code == 409
    assert response.json()["error"]["retryable"] is False
    assert state(app)["records"] == []
    with app.state.tasks.transaction() as db:
        assert db.execute(
            "SELECT model_calls,status FROM extraction_attempts"
        ).fetchone() == (1, "failed")
        assert db.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0] == 0


def test_foreign_evidence_rejects_batch_even_for_human_protected_key(tmp_path):
    app, c = setup(
        tmp_path, lambda ctx: [candidate(report_id="r1", evidence_ids=["foreign"])]
    )
    record = rec(metric_id="E01", year=2024, report_id="r1", task_id="t1")
    app.state.records.add(record["record_id"], record)
    response = post(c)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FOREIGN_EVIDENCE"
    assert len(state(app)["records"]) == 1
    assert app.state.tasks.get("t1").revision == 3


def test_compare_persists_reuses_and_rejects_stale(tmp_path):
    app, c = setup(tmp_path)
    for year in (2023, 2024):
        record = rec(year=year, report_id="r1", task_id="t1")
        app.state.records.add(record["record_id"], record)
    args = dict(
        expected_revision=3, older_record_id="r_E04_2023", newer_record_id="r_E04_2024"
    )
    first = post(c, "compare", args).json()
    assert validate("compare_years_output", first), first
    assert first["revision"] == 4
    args["expected_revision"] = 4
    again = post(c, "compare", args).json()
    assert again["data"] == first["data"]
    assert again["revision"] == 4
    assert len(state(app)["comparisons"]) == 1


def test_check_batch_and_human_review_survive_restart(tmp_path):
    app, c = setup(tmp_path)
    proposal = dict(
        check_id="C01",
        rule_version="disclosure-v0.1",
        status="unknown",
        evidence_ids=[],
        reason="待核实",
        missing_information=[],
        suggestion="",
        target_key=None,
    )
    response = post(c, "checks", dict(expected_revision=3, checks=[proposal]))
    assert response.status_code == 200, response.text
    reviewed = c.post(
        "/tasks/t1/review",
        headers={"x-user": "local_user"},
        json=dict(
            expected_revision=4, check_id="C01", new_status="met", reason="人工核对"
        ),
    )
    assert reviewed.status_code == 200, reviewed.text
    restarted = create_app(Settings(database_path=str(tmp_path / "test.db")))
    c2 = TestClient(restarted)
    conflict = post(c2, "checks", dict(expected_revision=5, checks=[proposal]))
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "INVALID_STATE"
    assert state(restarted)["checks"][0]["origin"] == "manual"
    assert len(restarted.state.review_store.audit_events("t1")) == 1


@pytest.mark.parametrize(
    "new",
    [
        dict(unit="kWh", value="100000"),
        dict(method_text="changed"),
        dict(
            denominator={
                "kind": "revenue",
                "currency": "USD",
                "scale": "1",
                "unit": "万元营收",
            }
        ),
    ],
)
def test_comparison_does_not_assume_equal_units_methods_or_denominators(new):
    from esg_app.comparison import compare_records

    old = (
        rec(
            value="100",
            denominator={
                "kind": "revenue",
                "currency": "CNY",
                "scale": "1",
                "unit": "万元营收",
            },
        )
        if "denominator" in new
        else rec(value="100")
    )
    result = compare_records(old, rec(year=2024, **new))
    if new.get("unit") == "kWh":
        assert result["percent_change"] == "0.00"
    else:
        assert result["percent_change"] is None


def test_fingerprint_cache_reuses_records_without_model_or_revision(tmp_path):
    calls = []
    app, c = setup(
        tmp_path,
        lambda ctx: calls.append(1)
        or [candidate(report_id="r1", evidence_ids=["ev1"])],
    )
    first = post(c).json()
    second = post(
        c, args=dict(expected_revision=4, metric_ids=["E01"], years=[2024])
    ).json()
    assert second["revision"] == 4
    assert second["data"]["records"] == first["data"]["records"]
    assert second["data"]["run"]["usage"]["model_calls"] == 0
    assert len(calls) == 1


def test_unconfigured_model_and_failed_calls_do_not_become_empty_success(tmp_path):
    app, c = setup(tmp_path, None)
    key = str(uuid4())
    unavailable = post(c, key=key)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["retryable"] is True
    assert not state(app)["records"]
    app.state.extraction_deps.model_fn = lambda ctx: []
    assert post(c, key=key).status_code == 200


def test_budget_includes_conflicted_calls(tmp_path):
    from dataclasses import replace

    app, c = setup(tmp_path)
    app.state.settings = replace(app.state.settings, max_extraction_model_calls=1)

    def model(ctx):
        app.state.tasks.get("t1").revision += 1
        return []

    app.state.extraction_deps.model_fn = model
    assert post(c).status_code == 409
    response = post(c, args=dict(expected_revision=4, metric_ids=["E01"], years=[2024]))
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "BUDGET_EXCEEDED"


def test_export_version_changed_during_generation_is_not_published(
    tmp_path, monkeypatch
):
    import esg_app.exports as exports

    app, c = setup(tmp_path)
    original = exports._render_csv

    def change(records, dispositions=None):
        app.state.tasks.get("t1").revision = 4
        return original(records, dispositions)

    monkeypatch.setattr(exports, "_render_csv", change)
    response = post(c, "exports", dict(expected_revision=3))
    assert response.status_code == 409, response.text
    assert not app.state.artifacts.list_for_task("t1")
    assert not list(app.state.artifacts.root.iterdir())
    with app.state.tasks.transaction() as db:
        assert not db.execute("SELECT 1 FROM idempotency").fetchone()


def test_export_build_failure_cleanup_then_same_key_retry(tmp_path, monkeypatch):
    from esg_app.exports import ArtifactStore

    app, c = setup(tmp_path)
    original = ArtifactStore.add
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("offline injected disk failure")
        return original(*args, **kwargs)

    key = str(uuid4())
    with monkeypatch.context() as m:
        m.setattr(ArtifactStore, "add", fail_second)
        response = post(c, "exports", dict(expected_revision=3), key)
    assert response.status_code == 500
    assert not app.state.artifacts.list_for_task("t1")
    assert not list(app.state.artifacts.root.iterdir())
    retried = post(c, "exports", dict(expected_revision=3), key)
    assert retried.status_code == 200, retried.text


def test_export_replay_restart_and_stale_download(tmp_path):
    import hashlib

    app, c = setup(tmp_path)
    key = str(uuid4())
    first = post(c, "exports", dict(expected_revision=3), key).json()
    assert validate("export_analysis_output", first)
    restarted = create_app(Settings(database_path=str(tmp_path / "test.db")))
    c2 = TestClient(restarted)
    assert post(c2, "exports", dict(expected_revision=3), key).json() == first
    assert restarted.state.tasks.get("t1").revision == 3
    restarted.state.tasks.get("t1").revision = 4
    for artifact in first["data"]["artifacts"]:
        response = c2.get(artifact["download_url"], headers={"X-User": "local_user"})
        assert response.status_code == 200
        assert response.headers["x-artifact-current"] == "false"
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        assert c2.get(artifact["download_url"]).status_code == 401


def test_validate_and_score_are_read_only_and_results_are_live(tmp_path):
    app, c = setup(tmp_path)
    first = post(c).json()
    rid = first["data"]["records"][0]["record_id"]
    headers = {"Authorization": "Bearer token"}
    validated = c.post(
        "/api/tasks/t1/validate", headers=headers, json=dict(record_ids=[rid])
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["data"]["status"] == "needs_review"
    score = c.get("/api/tasks/t1/score", headers=headers)
    assert score.status_code == 200, score.text
    assert score.json()["data"]["coverage"] == "0"
    assert app.state.tasks.get("t1").revision == 4
    assert (
        c.post(
            "/api/tasks/t1/validate", headers=headers, json=dict(record_ids=["foreign"])
        ).status_code
        == 404
    )


def test_two_connections_serialize_same_task_model_claim(tmp_path):
    started, release = Event(), Event()
    calls = []

    def model(ctx):
        calls.append(1)
        started.set()
        assert release.wait(10)
        return []

    app, c1 = setup(tmp_path, model)
    app2 = create_app(Settings(database_path=str(tmp_path / "test.db")))
    app2.state.extraction_deps = app.state.extraction_deps
    c2 = TestClient(app2)
    key = str(uuid4())
    with ThreadPoolExecutor() as pool:
        first = pool.submit(post, c1, key=key)
        assert started.wait(5)
        try:
            response = post(c2, key=key)
            assert response.status_code == 422
        finally:
            release.set()
        assert first.result().status_code == 200
    assert len(calls) == 1
    assert post(c2, key=key).status_code == 200


def test_compare_mismatched_report_or_metric_never_outputs_yoy():
    from esg_app.comparison import compare_records

    for new in (rec(year=2024, report_id="other"), rec(year=2024, metric_id="E01")):
        result = compare_records(rec(), new)
        assert result["status"] == "not_comparable"
        assert result["percent_change"] is None


def test_task_results_uses_registered_metadata_and_current_durable_state(tmp_path):
    app, c = setup(tmp_path)
    root = Path(__file__).resolve().parents[1]
    sample = json.loads(
        (root / "contracts/examples/get_task_results.success.json").read_text()
    )["data"]
    sample["task"].update(task_id="t1", report_ids=["r1"], revision=3)
    sample["reports"][0]["report_id"] = "r1"
    sample["private_path"] = "/private/never-return"
    with app.state.tasks.transaction() as db:
        db.execute(
            "UPDATE tasks SET results=? WHERE task_id=?", (json.dumps(sample), "t1")
        )
    first = post(c).json()
    result = c.get("/api/tasks/t1/results", headers={"Authorization": "Bearer token"})
    assert result.status_code == 200, result.text
    assert validate("get_task_results_output", result.json())
    assert result.json()["data"]["task"]["revision"] == 4
    assert result.json()["data"]["metrics"] == first["data"]["records"]
    assert len(result.json()["data"]["extraction_runs"]) == 1
    assert "/private/" not in result.text
    c.get("/api/tasks/t1/score", headers={"Authorization": "Bearer token"})
    assert (
        c.get(
            "/api/tasks/t1/results", headers={"Authorization": "Bearer token"}
        ).json()["data"]["score"]
        is not None
    )


def test_reparse_invalidates_records_checks_comparisons_and_score(tmp_path):
    from test_parse_contract import pdf_bytes
    from esg_app.parse_repository import save_business

    app, c = setup(tmp_path)
    record = rec(report_id="r1", task_id="t1")
    app.state.records.add(record["record_id"], record)
    with app.state.tasks.transaction() as db:
        business = read_business(db, "t1")
        business["checks"] = [dict(check_id="C01", status="met", is_current=True)]
        business["comparisons"] = [dict(comparison_id="c1", is_current=True)]
        business["score"] = dict(revision=3)
        save_business(db, "t1", business)
    path = tmp_path / "new.pdf"
    path.write_bytes(pdf_bytes())
    app.state.tasks.register_report_path("r1", path)
    response = c.post(
        "/api/tasks/t1/parse",
        json={"expected_revision": 3},
        headers={"Authorization": "Bearer token", "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    business = state(app)
    for kind in ("records", "checks", "comparisons"):
        assert not any(item["is_current"] for item in business[kind])
    assert business.get("score") is None


def test_export_owner_is_required_even_for_other_authenticated_user(tmp_path):
    app, c = setup(tmp_path)
    response = c.post(
        "/tasks/t1/exports", json={"expected_revision": 3}, headers={"X-User": "alice"}
    )
    assert response.status_code == 200, response.text
    url = response.json()["data"]["artifacts"][0]["download_url"]
    assert c.get(url, headers={"X-User": "bob"}).status_code == 404
    assert c.get(url, headers={"X-User": "alice"}).status_code == 200


def test_checks_rule_version_and_duplicate_batch_leave_no_partial_write(tmp_path):
    app, c = setup(tmp_path)
    proposal = dict(
        check_id="C01",
        rule_version="wrong",
        status="unknown",
        evidence_ids=[],
        reason="待核对",
        missing_information=[],
        suggestion="",
        target_key=None,
    )
    assert (
        post(c, "checks", dict(expected_revision=3, checks=[proposal])).status_code
        == 409
    )
    proposal["rule_version"] = "disclosure-v0.1"
    assert (
        post(
            c, "checks", dict(expected_revision=3, checks=[proposal, proposal])
        ).status_code
        == 422
    )
    assert not state(app)["checks"]
    assert app.state.tasks.get("t1").revision == 3


def test_agent_bearer_cannot_use_existing_cookie_to_review_or_download(tmp_path):
    app, c = setup(tmp_path)
    c.get("/")
    response = c.post(
        "/tasks/t1/review",
        headers={"Authorization": "Bearer token"},
        json=dict(
            expected_revision=3, check_id="C01", new_status="met", reason="model"
        ),
    )
    assert response.status_code == 403
    exported = post(c, "exports", dict(expected_revision=3)).json()
    url = exported["data"]["artifacts"][0]["download_url"]
    assert c.get(url, headers={"Authorization": "Bearer token"}).status_code == 403


def test_non_string_tool_rejected_with_contract_envelope(tmp_path):
    _, c = setup(tmp_path)
    response = c.post(
        "/agent/tool",
        headers={"X-Agent-Token": "token"},
        json={"tool": [], "arguments": {}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_history_capacity_failure_does_not_publish_partial_comparison(tmp_path):
    from esg_app.parse_repository import save_business

    app, c = setup(tmp_path)
    for year in (2023, 2024):
        record = rec(year=year, report_id="r1", task_id="t1")
        app.state.records.add(record["record_id"], record)
    with app.state.tasks.transaction() as db:
        business = read_business(db, "t1")
        business["comparisons"] = [
            dict(
                comparison_id="old_" + str(i),
                older_record_id="other",
                newer_record_id="other",
                is_current=False,
            )
            for i in range(100)
        ]
        save_business(db, "t1", business)
    response = post(
        c,
        "compare",
        dict(
            expected_revision=3,
            older_record_id="r_E04_2023",
            newer_record_id="r_E04_2024",
        ),
    )
    assert response.status_code == 422
    assert len(state(app)["comparisons"]) == 100
    assert app.state.tasks.get("t1").revision == 3


@pytest.mark.parametrize("bad", ["unit", "year"])
def test_validate_detects_bad_unit_or_evidence_year_without_changing_records(
    tmp_path, bad
):
    app, c = setup(tmp_path)
    record = rec(
        metric_id="E01",
        year=2024,
        report_id="r1",
        task_id="t1",
        evidence_ids=["ev1"],
        unit="tCO2e",
    )
    if bad == "unit":
        record["raw_unit"] = "MWh"
    else:
        app.state.evidence.items[0].column_year = 2023
    app.state.records.add(record["record_id"], record)
    before = state(app)["records"]
    response = c.post(
        "/api/tasks/t1/validate",
        headers={"Authorization": "Bearer token"},
        json=dict(record_ids=[record["record_id"]]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "invalid"
    assert state(app)["records"] == before
    assert app.state.tasks.get("t1").revision == 3
