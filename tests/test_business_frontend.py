"""SSR/browser contract checks, with no live provider calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/src"))
from test_web import build_client
from esg_app.web_presentation import evidence_view, present, percent
from esg_app.retrieval import Evidence


def test_business_page_does_not_load_demo_or_agent_routes():
    client = build_client()
    html = client.get("/?task_id=task_web").text
    assert "/static/js/business.js" in html
    assert "demo-data" not in html
    assert "/agent/tool" not in html
    assert "tok_web" not in html
    assert 'data-revision="7"' in html
    assert 'data-operation="review"' in html
    assert 'data-draft="check-C01"' in html
    assert "运行宿主未配置" in html
    assert client.get("/api/tasks/task_web/results").status_code == 403
    assert client.get("/").headers["cache-control"] == "no-store"


def test_no_task_does_not_fallback_to_mock():
    from esg_app.main import create_app
    from esg_app.config import Settings
    from fastapi.testclient import TestClient

    html = TestClient(create_app(Settings())).get("/").text
    assert "当前没有任务" in html
    assert "比亚迪" not in html
    assert "data-operation=" not in html


def test_zero_empty_null_and_precision_remain_distinct():
    assert present("0") == "0"
    assert present("0.1300") == "0.1300"
    assert present("") == "空字符串"
    assert present(None) == "未提供"
    assert percent("0") == "0.00%"
    assert percent(None) == "未定"
    assert percent("0.125") == "12.50%"
    client = build_client()
    for c in client.app.state.checks["task_web"]:
        c.update(status="not_met", reason="已人工核对不满足")
    html = client.get("/").text
    assert "最终分" in html
    assert "参考分</dt><dd>0.00</dd>" in html


def test_evidence_uses_bound_report_not_untrusted_viewer_url():
    ev = Evidence(
        "evil",
        "rpt_1",
        127,
        None,
        "footnote",
        "<img src=x onerror=alert(1)>",
        viewer_url="javascript:alert(1)",
    )
    assert evidence_view(ev)["viewer_url"] == "/api/reports/rpt_1/pdf#page=127"
    ev.pdf_page = 0
    assert evidence_view(ev)["viewer_url"] is None
    ev.pdf_page = 1
    ev.report_id = "../secret"
    assert evidence_view(ev)["viewer_url"] is None
    client = build_client()
    client.app.state.evidence.add(
        Evidence(
            "evil",
            "rpt_1",
            127,
            None,
            "text",
            "<img src=x onerror=alert(1)>",
            viewer_url="https://evil.example/",
        )
    )
    html = client.get("/").text
    assert "https://evil.example/" not in html
    assert "&lt;img" in html and "<img src=x" not in html


def test_review_conflict_preserves_server_state_and_refreshes_versions():
    client = build_client()
    client.get("/")
    body = {
        "check_id": "C01",
        "new_status": "unknown",
        "reason": "核对原文后仍缺证据",
        "expected_revision": 7,
    }
    assert client.post("/tasks/task_web/review", json=body).json()["revision"] == 8
    assert client.post("/tasks/task_web/review", json=body).status_code == 409
    html = client.get("/").text
    assert 'data-revision="8"' in html
    assert 'name="expected_revision" value="7"' not in html
    assert "指标（待接口）" in html or "指标修正等待正式用户接口" in html


def test_status_and_answers_are_persisted_not_simulated():
    client = build_client()
    runs = client.app.state.runs
    run = runs.start("task_web", "private-session-not-for-browser")
    runs.fail(run.run_id, "<script>not executable</script>")
    html = client.get("/").text
    assert "运行失败" in html
    assert "&lt;script&gt;not executable" in html
    assert "private-session-not-for-browser" not in html
    assert 'data-run-status="failed"' in html


def test_final_zero_is_rendered_as_zero(monkeypatch):
    import esg_app.main as main

    client = build_client()
    monkeypatch.setattr(
        main,
        "do_score",
        lambda *args: dict(
            coverage="1",
            reference_score="0",
            lower_bound="0",
            upper_bound="0",
            final_score="0",
            label="final_score",
        ),
    )
    html = client.get("/").text
    assert "最终分</dt><dd>0.00</dd>" in html
    assert "证据覆盖率</dt><dd>100.00%</dd>" in html


def test_download_sets_attachment_filename_and_revision():
    client = build_client()
    client.get("/")
    result = client.post(
        "/tasks/task_web/exports", json={"expected_revision": 7}
    ).json()
    for artifact in result["data"]["artifacts"]:
        response = client.get(artifact["download_url"])
        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith(
            "attachment; filename*=UTF-8"
        )
        assert response.headers["x-artifact-revision"] == "7"
        assert response.headers["x-artifact-current"] == "true"
