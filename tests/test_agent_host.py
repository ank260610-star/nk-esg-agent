import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from esg_app.agent_host import make_agent_starter
from esg_app.main import create_app
from esg_app.config import Settings
from esg_app.run_jobs import RunStatus
from esg_app.parse_repository import read_business, save_business


def app_with_task(tmp_path):
    app = create_app(Settings(database_path=str(tmp_path / "db")))
    root = Path(__file__).resolve().parents[1]
    data = json.loads(
        (root / "contracts/examples/get_task_results.success.json").read_text()
    )["data"]
    data["task"].update(task_id="t1", revision=3)
    app.state.tasks.register_task("t1", 3, data)
    run = app.state.runs.start("t1", "session_1")
    return app, run


def test_sdk_prose_without_real_backend_tool_cannot_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    app, run = app_with_task(tmp_path)
    starter = make_agent_starter(
        app,
        lambda *_a, **_k: SimpleNamespace(
            finish_reason="completed", final_response="全部完成"
        ),
    )
    with pytest.raises(RuntimeError, match="No successful current"):
        starter("t1", run.run_id, "full_evaluation", "data")
    assert run.status is not RunStatus.COMPLETED


def test_host_passes_bound_token_and_only_completes_after_server_audit(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    app, run = app_with_task(tmp_path)

    def sdk(settings, prompt, session_id, **kwargs):
        assert kwargs["task_id"] == "t1" and session_id == "session_1"
        assert "offline-key" not in prompt and kwargs["token"] not in prompt
        assert prompt.lstrip().startswith("#")
        response = TestClient(app).post(
            "/agent/tool",
            headers={"X-Agent-Token": kwargs["token"]},
            json=dict(tool="get_task_results", arguments={}),
        )
        assert response.status_code == 200, response.text
        return SimpleNamespace(finish_reason="completed", final_response="证据仍待核对")

    starter = make_agent_starter(app, sdk)
    starter("t1", run.run_id, "question", '{"user_question":"/permission"}')
    assert app.state.runs.get(run.run_id).status is RunStatus.COMPLETED
    assert app.state.runs.get(run.run_id).revision == 3
    with app.state.tasks.transaction() as db:
        row = db.execute("SELECT identity,tool,ok FROM agent_tool_calls").fetchone()
        assert row[1:] == ("get_task_results", 1)
        assert len(row[0]) == 64


def test_unreviewed_metrics_yield_waiting_review_not_completed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    app, run = app_with_task(tmp_path)

    def sdk(_settings, _prompt, _session_id, **kwargs):
        c = TestClient(app)
        c.post(
            "/agent/tool",
            headers={"X-Agent-Token": kwargs["token"]},
            json=dict(tool="get_task_results", arguments={}),
        )
        return SimpleNamespace(
            finish_reason="completed", final_response="已提取，等待人工复核"
        )

    # Actual persisted metric structure is supplied by the canonical offline fixture.
    data = app.state.tasks.get("t1").results
    with app.state.tasks.transaction() as db:
        state = read_business(db, "t1")
        state["records"] = data["metrics"]
        for record in state["records"]:
            record["review_state"] = "unreviewed"
        save_business(db, "t1", state)
    make_agent_starter(app, sdk)("t1", run.run_id, "full_evaluation", "data")
    assert app.state.runs.get(run.run_id).status is RunStatus.WAITING_REVIEW


def test_missing_key_never_starts_sdk(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app, run = app_with_task(tmp_path)
    called = []
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        make_agent_starter(app, lambda *_a, **_k: called.append(1))(
            "t1", run.run_id, "question", "data"
        )
    assert not called


def test_run_state_and_exclusive_claim_survive_restart(tmp_path):
    app, run = app_with_task(tmp_path)
    restored = create_app(Settings(database_path=str(tmp_path / "db")))
    assert restored.state.runs.get(run.run_id).session_id == run.session_id
    assert restored.state.runs.get(run.run_id).status is RunStatus.RUNNING
    with pytest.raises(RuntimeError, match="active"):
        restored.state.runs.create_queued("t1", "another_session")
    restored.state.runs.fail(run.run_id, "worker was reconciled as stopped")
    next_run = restored.state.runs.create_queued("t1", "next_session")
    assert next_run.status is RunStatus.QUEUED


def test_review_paused_session_is_reused_by_user_resume_route(tmp_path):
    import time

    app, run = app_with_task(tmp_path)
    run.status = RunStatus.WAITING_REVIEW
    app.state.runs.save(run)
    restored = create_app(Settings(database_path=str(tmp_path / "db")))
    seen = []

    def starter(task_id, run_id, mode, prompt):
        seen.append((restored.state.runs.get(run_id).session_id, mode, prompt))
        restored.state.runs.fail(run_id, "offline stub stops here")

    restored.state.agent_starter = starter
    response = TestClient(restored).post(
        "/tasks/t1/agent-runs",
        headers={"X-User": "local_user"},
        json=dict(mode="resume", expected_revision=3, question="/permission"),
    )
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 3
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen[0][0] == run.session_id and seen[0][1] == "resume"
    assert json.loads(seen[0][2])["user_question"] == "/permission"


@pytest.mark.parametrize("finish", ["error", "blocked", "max-tokens", "interrupted"])
def test_abnormal_sdk_finish_never_becomes_completed(tmp_path, monkeypatch, finish):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    app, run = app_with_task(tmp_path)
    starter = make_agent_starter(
        app,
        lambda *_a, **_k: SimpleNamespace(
            finish_reason=finish, final_response="旧文本或错误诊断"
        ),
    )
    with pytest.raises(RuntimeError, match="normal turn"):
        starter("t1", run.run_id, "question", "data")
    assert app.state.runs.get(run.run_id).status is not RunStatus.COMPLETED


def test_incomplete_six_metric_coverage_cannot_be_completed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-key")
    app, run = app_with_task(tmp_path)
    data = app.state.tasks.get("t1").results
    with app.state.tasks.transaction() as db:
        state = read_business(db, "t1")
        state["records"] = data["metrics"][:1]
        state["checks"] = data["checks"]
        for r in state["records"]:
            r["review_state"] = "confirmed"
        for c in state["checks"]:
            c.update(status="met", review_state="confirmed")
        save_business(db, "t1", state)

    def sdk(*_a, **kwargs):
        result = TestClient(app).post(
            "/agent/tool",
            headers={"X-Agent-Token": kwargs["token"]},
            json=dict(tool="get_task_results", arguments={}),
        )
        assert result.status_code == 200, result.text
        return SimpleNamespace(finish_reason="completed", final_response="报告已完成")

    make_agent_starter(app, sdk)("t1", run.run_id, "full_evaluation", "data")
    assert app.state.runs.get(run.run_id).status is RunStatus.WAITING_REVIEW
