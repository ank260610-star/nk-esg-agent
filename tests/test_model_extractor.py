import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from esg_app.model_extractor import DeepSeekExtractor, ModelOutputError
from esg_app.retrieval import Evidence, EvidenceStore
from esg_app.config import Settings
from esg_app.main import create_app
from test_metrics import candidate
from test_write_contract import post


def evidence():
    return Evidence(
        "ev1",
        "r1",
        1,
        None,
        "table",
        "温室气体排放量（范围1和范围2） 2024 100 tCO2e",
        column_year=2024,
    )


def response(candidates=None, finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "content": json.dumps(
                        {
                            "candidates": (
                                candidates
                                if candidates is not None
                                else [
                                    candidate(
                                        report_id="r1",
                                        evidence_ids=["ev1"],
                                        value="100",
                                        raw_unit="tCO2e",
                                    )
                                ]
                            )
                        }
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 123, "completion_tokens": 45},
    }


def test_adapter_sends_only_backend_scoped_evidence_and_records_usage(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    seen = []

    def transport(payload, key, timeout):
        seen.append(payload)
        assert key == "offline-key"
        return response()

    model = DeepSeekExtractor(
        lambda _: [
            evidence(),
            Evidence("foreign", "outside", 1, None, "text", "温室气体排放量"),
        ],
        "deepseek-flash",
        transport=transport,
    )
    result = model(
        {
            "task_id": "t",
            "metric_ids": ["E01"],
            "years": [2024],
            "allowed_evidence_ids": ["ev1"],
        }
    )
    assert result.usage == dict(model_calls=1, input_tokens=123, output_tokens=45)
    payload = seen[0]
    assert payload["response_format"] == {"type": "json_object"}
    data = json.loads(payload["messages"][1]["content"])
    assert [e["evidence_id"] for e in data["untrusted_evidence"]] == ["ev1"]
    assert data["trusted_config"]["allowed_evidence_ids"] == ["ev1"]
    assert not any("GOLD" in m["content"] for m in payload["messages"])


@pytest.mark.parametrize("bad", ["empty", "length", "foreign", "malformed"])
def test_invalid_provider_output_does_not_become_success(monkeypatch, bad):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    r = response()
    if bad == "empty":
        r = response([])
    if bad == "length":
        r = response(finish="length")
    if bad == "foreign":
        r = response([candidate(report_id="r1", evidence_ids=["invented"])])
    if bad == "malformed":
        r["choices"][0]["message"]["content"] = "not json"
    model = DeepSeekExtractor(
        lambda _: [evidence()], "deepseek-flash", transport=lambda *_: r
    )
    with pytest.raises(ModelOutputError) as error:
        model(
            {
                "task_id": "t",
                "metric_ids": ["E01"],
                "years": [2024],
                "allowed_evidence_ids": ["ev1"],
            }
        )
    assert error.value.usage["input_tokens"] == 123


def test_live_adapter_usage_persists_on_version_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    app = create_app(
        Settings(database_path=str(tmp_path / "db"), extraction_model="deepseek-flash")
    )
    app.state.tasks.register_task(
        "t1", 3, dict(report_ids=["r1"], selected_years=[2024])
    )
    app.state.tasks.issue_agent_token("t1", "token")
    app.state.evidence = EvidenceStore([evidence()])

    def transport(*_):
        app.state.tasks.get("t1").revision = 4
        return response()

    app.state.extraction_deps.model_fn.transport = transport
    r = post(TestClient(app))
    assert r.status_code == 409, r.text
    with app.state.tasks.transaction() as db:
        calls, usage, status = db.execute(
            "SELECT model_calls,usage,status FROM extraction_attempts"
        ).fetchone()
        assert calls == 1 and status == "failed"
        assert json.loads(usage)["input_tokens"] == 123
    assert app.state.records.list_by_task("t1") == []


def test_no_evidence_does_not_charge_model_call(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    app = create_app(
        Settings(database_path=str(tmp_path / "db"), extraction_model="deepseek-flash")
    )
    app.state.tasks.register_task(
        "t1", 3, dict(report_ids=["r1"], selected_years=[2024])
    )
    app.state.tasks.issue_agent_token("t1", "token")
    r = post(TestClient(app))
    assert r.status_code == 503
    with app.state.tasks.transaction() as db:
        assert db.execute("SELECT model_calls FROM extraction_attempts").fetchone() == (
            0,
        )


def test_packaged_prompt_matches_backend_prompt():
    root = Path(__file__).resolve().parents[1]
    assert (root / "backend/prompts/metric-extraction.md").read_bytes() == (
        root / "backend/src/esg_app/prompts/metric-extraction.md"
    ).read_bytes()


def test_packaged_agent_prompt_matches_plugin_prompt():
    root = Path(__file__).resolve().parents[1]
    assert (root / "harness-plugin/prompts/esg-agent.md").read_bytes() == (
        root / "backend/src/esg_app/prompts/esg-agent.md"
    ).read_bytes()


@pytest.mark.parametrize("bad", ["number", "conversion", "missing_marker"])
def test_unverifiable_model_values_become_unknown_with_evidence_issue(monkeypatch, bad):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    c = candidate(report_id="r1", evidence_ids=["ev1"], value="100", raw_unit="tCO2e")
    if bad == "number":
        c.update(raw_value="999", value="999")
    if bad == "conversion":
        c.update(canonical_unit="MWh")
    if bad == "missing_marker":
        c.update(raw_value="/", value=None, status="missing_marker")
    model = DeepSeekExtractor(
        lambda _: [evidence()], "deepseek-flash", transport=lambda *_: response([c])
    )
    result = model(
        dict(
            task_id="t", metric_ids=["E01"], years=[2024], allowed_evidence_ids=["ev1"]
        )
    )
    assert result[0]["status"] == "unknown" and result[0]["value"] is None
    assert result.issues[0]["code"] == "MODEL_VALUE_UNVERIFIED"
    assert result.usage["input_tokens"] == 123


@pytest.mark.parametrize("year,value", [(2023, "0.2049"), (2024, "0.1300")])
def test_compound_unit_alias_survives_adapter_candidate_validation(monkeypatch, year, value):
    """Explicit offline candidates exercise both E02 years through the adapter."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    raw_unit = "吨二氧化碳当量/万元营收"
    cited = Evidence(
        "ev1", "r1", 127, None, "table",
        f"温室气体排放密度（范围1+范围2） {year} {value} {raw_unit}",
        column_year=year,
    )
    proposed = candidate(
        metric_id="E02", year=year, value=value,
        raw_unit=raw_unit, canonical_unit="tCO2e/万元营收",
        report_id="r1", evidence_ids=["ev1"],
    )
    model = DeepSeekExtractor(
        lambda _: [cited], "deepseek-flash",
        transport=lambda *_: response([proposed]),
    )
    result = model(dict(
        task_id="t", metric_ids=["E02"], years=[year],
        allowed_evidence_ids=["ev1"],
    ))
    assert result[0]["status"] == "extracted"
    assert result[0]["raw_value"] == result[0]["value"] == value
    assert result[0]["evidence_ids"] == ["ev1"]
    assert result.issues == []
