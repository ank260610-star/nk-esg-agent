"""Task 6 网页失败测试：四区域、PDF定位、复核后失效、下载安全、HTML转义。"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from esg_app.config import Settings  # noqa: E402
from esg_app.main import create_app  # noqa: E402
from esg_app.repository import InMemoryTaskStore  # noqa: E402
from esg_app.retrieval import Evidence, EvidenceStore  # noqa: E402


def build_client():
    app = create_app(Settings())
    store: InMemoryTaskStore = app.state.tasks
    store.register_task("task_web", 7, {"records": [], "checks": [],
                                        "report_ids": ["rpt_1"]})
    store.issue_agent_token("task_web", "tok_web")
    app.state.evidence = EvidenceStore([
        Evidence("ev1", "rpt_1", 127, "126", "table",
                 "温室气体排放量 10,101,826.20",
                 viewer_url="/api/reports/rpt_1/pdf#page=127"),
        Evidence("ev_xss", "rpt_1", 3, None, "text",
                 "<script>alert('xss')</script>用户注入",
                 viewer_url="/api/reports/rpt_1/pdf#page=3"),
    ])
    app.state.checks["task_web"] = [
        {"check_id": "C01", "status": "met", "origin": "manual",
         "review_state": "confirmed", "evidence_ids": ["ev1"],
         "revision": 7, "is_current": True},
    ]
    return TestClient(app)


class TestBusinessPages:
    def test_index_has_four_areas_without_tools_or_keys(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-SHOULD-NOT-APPEAR")
        c = build_client()
        r = c.get("/")
        assert r.status_code == 200
        html = r.text
        for token in ("报告导入", "指标与证据", "评分与未知项", "人工复核"):
            assert token in html
        assert "sk-SHOULD-NOT-APPEAR" not in html
        assert "get_task_results" not in html
        assert "pwsh" not in html

    def test_user_input_is_html_escaped(self):
        c = build_client()
        html = c.get("/").text
        assert "<script>alert('xss')</script>" not in html
        assert "&lt;script&gt;" in html

    def test_evidence_links_point_to_registered_pdf_page(self):
        c = build_client()
        html = c.get("/").text
        assert "/api/reports/rpt_1/pdf#page=127" in html

    def test_unknown_task_returns_404(self):
        c = build_client()
        assert c.get("/?task_id=not_a_task").status_code == 404

    def test_agent_run_requires_user_session(self):
        c = build_client()
        r = c.post("/tasks/task_web/agent-runs",
                   headers={"x-agent-token": "tok_web"},
                   json={"mode": "full_evaluation", "expected_revision": 7})
        assert r.status_code == 403

    def test_agent_run_start_and_status(self):
        c = build_client()
        r = c.post("/tasks/task_web/agent-runs",
                   headers={"x-user": "reviewer"},
                   json={"mode": "full_evaluation", "expected_revision": 7})
        assert r.status_code == 200, r.text
        run_id = r.json()["data"]["run_id"]
        status = c.get(f"/tasks/task_web/agent-runs/{run_id}",
                       headers={"x-user": "reviewer"})
        assert status.status_code == 200
        assert status.json()["data"]["status"] in {"queued", "running", "completed", "failed"}
        # 跨任务不能读别人的 run
        assert c.get(f"/tasks/other/agent-runs/{run_id}",
                     headers={"x-user": "reviewer"}).status_code in (403, 404)


class TestReviewAndExport:
    def test_review_invalidates_exports_and_page_marks_stale(self):
        c = build_client()
        exp = c.post("/tasks/task_web/exports",
                     headers={"x-user": "reviewer"},
                     json={"expected_revision": 7})
        assert exp.status_code == 200, exp.text
        artifacts = exp.json()["data"]["artifacts"]
        html = c.get("/").text
        assert "当前版本" in html
        r = c.post("/tasks/task_web/review",
                   headers={"x-user": "reviewer"},
                   json={"check_id": "C01", "new_status": "partial",
                         "reason": "人工复核证据不足", "expected_revision": 7})
        assert r.status_code == 200, r.text
        html = c.get("/").text
        assert "已过期" in html

    def test_browser_form_review_via_cookie_session(self):
        """浏览器表单没有 x-user header：靠首页 cookie 会话完成复核。"""
        c = build_client()
        c.get("/")  # 建立 esg_user cookie
        r = c.post("/tasks/task_web/review",
                   data={"check_id": "C01", "new_status": "not_met",
                         "reason": "表单复核理由", "expected_revision": "7"})
        assert r.status_code == 200, r.text
        assert r.json()["revision"] == 8

    def test_export_form_via_cookie_session(self):
        c = build_client()
        c.get("/")
        r = c.post("/tasks/task_web/exports", data={"expected_revision": "7"})
        assert r.status_code == 200, r.text
        assert len(r.json()["data"]["artifacts"]) == 3

    def test_export_download_by_registered_id_only(self):
        c = build_client()
        exp = c.post("/tasks/task_web/exports",
                     headers={"x-user": "reviewer"},
                     json={"expected_revision": 7})
        art = exp.json()["data"]["artifacts"][0]
        # 匿名持有合法 URL 也不能下载；浏览器访问首页后靠 cookie 会话下载
        assert c.get(art["download_url"]).status_code == 401
        c.get("/")
        r = c.get(art["download_url"])
        assert r.status_code == 200
        assert r.headers["content-type"].startswith(art["media_type"].split(";")[0])
        assert c.get("/api/artifacts/../secret.pdf").status_code == 404
        assert c.get("/api/artifacts/art_missing").status_code == 404

    def test_export_rejects_wrong_revision(self):
        c = build_client()
        r = c.post("/tasks/task_web/exports",
                   headers={"x-user": "reviewer"},
                   json={"expected_revision": 6})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "REVISION_CONFLICT"

    def test_pdf_endpoint_only_serves_registered_report(self, tmp_path):
        c = build_client()
        pdf = tmp_path / "sample.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% fake for route guard test\n")
        c.app.state.report_paths = {"rpt_1": pdf}
        assert c.get("/api/reports/rpt_1/pdf").status_code == 401
        c.get("/")  # 浏览器会话 cookie
        assert c.get("/api/reports/rpt_1/pdf").status_code == 200
        # 任意路径/未登记 ID 不能下载
        assert c.get("/api/reports/rpt_unknown/pdf").status_code == 404
        assert c.get("/api/reports/%2e%2e%2fsecret/pdf").status_code == 404
