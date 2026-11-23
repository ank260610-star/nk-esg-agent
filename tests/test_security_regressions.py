"""自检回归测试（2026-11-22）：覆盖已确认的 8 类真 bug + 2 项边界。

每条测试对应一个独立复现场景，先红灯后修复。
"""
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from esg_app.comparison import ScenarioTransform, apply_scenario  # noqa: E402
from esg_app.config import Settings  # noqa: E402
from esg_app.exports import ArtifactStore, build_export_bundle  # noqa: E402
from esg_app.main import create_app  # noqa: E402
from esg_app.retrieval import Evidence, EvidenceStore  # noqa: E402


def _client_with_task(task_id="task_sec", revision=7, report_ids=None):
    app = create_app(Settings())
    app.state.tasks.register_task(task_id, revision,
                                  {"report_ids": report_ids or [], "records": [], "checks": []})
    app.state.tasks.issue_agent_token(task_id, f"tok_{task_id}")
    return TestClient(app)


class TestInputGuards:
    def test_arguments_must_be_object(self):
        """P0-1: arguments 传字符串不得 500。"""
        c = _client_with_task()
        r = c.post("/agent/tool", headers={"x-agent-token": "tok_task_sec"},
                   json={"tool": "search_evidence", "arguments": "bad"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_INPUT"

    def test_payload_must_be_object(self):
        """P0-1 衍生: payload 顶层是数组时不得 500。"""
        c = _client_with_task()
        r = c.post("/agent/tool", headers={"x-agent-token": "tok_task_sec"},
                   json=["search_evidence", {}])
        assert r.status_code == 400

    @pytest.mark.parametrize("bad_limit", ["5", 0, -1, 101, True])
    def test_search_limit_must_be_positive_int(self, bad_limit):
        """P0-1 衍生: limit 非正整数必须拒绝，不得 TypeError。"""
        c = _client_with_task()
        app = c.app
        app.state.evidence = EvidenceStore([
            Evidence("e1", "rpt", 1, None, "text", "温室气体排放")])
        r = c.post("/agent/tool", headers={"x-agent-token": "tok_task_sec"},
                   json={"tool": "search_evidence",
                         "arguments": {"query": "温室气体", "limit": bad_limit}})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_INPUT"


class TestReviewState:
    def test_review_keeps_other_checks(self):
        """P0-2: 复核一项不得清空其他检查项。"""
        c = _client_with_task()
        app = c.app
        app.state.checks["task_sec"] = [
            {"check_id": "C01", "status": "met", "origin": "manual",
             "review_state": "confirmed", "revision": 7, "is_current": True},
            {"check_id": "C02", "status": "met", "origin": "manual",
             "review_state": "confirmed", "revision": 7, "is_current": True},
        ]
        r = c.post("/tasks/task_sec/review", headers={"x-user": "u"},
                   json={"check_id": "C01", "new_status": "partial",
                         "reason": "证据不足", "expected_revision": 7})
        assert r.status_code == 200, r.text
        ids = {x["check_id"] for x in app.state.checks["task_sec"]}
        assert ids == {"C01", "C02"}, ids

    def test_review_invalidates_comparisons(self):
        """P1-6: 复核成功后旧比较结果必须标记过期。"""
        c = _client_with_task()
        app = c.app
        app.state.checks["task_sec"] = [
            {"check_id": "C01", "status": "met", "origin": "manual",
             "review_state": "confirmed", "revision": 7, "is_current": True}]
        app.state.comparisons_by_task["task_sec"] = [
            {"metric_id": "E01", "status": "comparable",
             "percent_change": "20.00", "is_current": True}]
        r = c.post("/tasks/task_sec/review", headers={"x-user": "u"},
                   json={"check_id": "C01", "new_status": "partial",
                         "reason": "重算", "expected_revision": 7})
        assert r.status_code == 200, r.text
        assert all(x["is_current"] is False
                   for x in app.state.comparisons_by_task["task_sec"])


class TestExportIsolation:
    def test_same_revision_bundles_do_not_overwrite(self, tmp_path):
        """P0-3: 同 revision 两次导出的文件不能互相覆盖，sha 必须对应内容。"""
        store = ArtifactStore(tmp_path / "artifacts")
        base = dict(task_id="t", expected_revision=7, current_revision=7,
                    checks=[], comparisons=[], evidence=[], rule_version="v1",
                    report_meta=[], score={}, store=store)
        b1 = build_export_bundle(**{**base,
                                    "records": [{"metric_id": "E01", "value": "AAA",
                                                 "raw_value": "AAA"}]})
        b2 = build_export_bundle(**{**base,
                                    "records": [{"metric_id": "E01", "value": "BBB",
                                                 "raw_value": "BBB"}]})
        a1 = next(a for a in b1["artifacts"] if a["format"] == "csv")
        a2 = next(a for a in b2["artifacts"] if a["format"] == "csv")
        assert a1["filename"] != a2["filename"]
        old_content = store.get_content(a1["artifact_id"])
        assert b"AAA" in old_content
        assert hashlib.sha256(old_content).hexdigest() == a1["sha256"]

    def test_export_evidence_scoped_to_task(self):
        """P1-7: 导出 JSON 不得收录其他任务的证据。"""
        c = _client_with_task(report_ids=["rpt_A"])
        app = c.app
        app.state.evidence = EvidenceStore([
            Evidence("evA", "rpt_A", 1, None, "text", "本任务"),
            Evidence("FOREIGN_EVIDENCE", "rpt_B", 1, None, "text", "他任务原文")])
        r = c.post("/tasks/task_sec/exports", headers={"x-user": "u"},
                   json={"expected_revision": 7})
        assert r.status_code == 200, r.text
        art = next(a for a in r.json()["data"]["artifacts"] if a["format"] == "json")
        text = app.state.artifacts.get_content(art["artifact_id"]).decode("utf-8")
        assert "FOREIGN_EVIDENCE" not in text
        assert "evA" in text


class TestAgentScope:
    def test_search_scoped_to_task(self):
        """独立自检发现：Agent 检索不得拿到其他任务的证据。"""
        c = _client_with_task("task_A", report_ids=["rpt_A"])
        app = c.app
        app.state.tasks.register_task("task_B", 1, {"report_ids": ["rpt_B"]})
        app.state.tasks.issue_agent_token("task_B", "tok_task_B")
        app.state.evidence = EvidenceStore([
            Evidence("evA", "rpt_A", 1, None, "text", "A_ONLY_SECRET"),
            Evidence("evB", "rpt_B", 1, None, "text", "B_ONLY_SECRET")])
        r = c.post("/agent/tool", headers={"x-agent-token": "tok_task_A"},
                   json={"tool": "search_evidence", "arguments": {"query": "B_ONLY_SECRET"}})
        assert r.status_code == 200, r.text
        assert "evB" not in [e["evidence_id"] for e in r.json()["data"]["evidence"]]

    def test_extract_whitelist_scoped_to_task(self):
        """独立自检发现：提取白名单不得含其他任务的证据 ID。"""
        from esg_app.extraction import ExtractionDeps
        c = _client_with_task("task_A", report_ids=["rpt_A"])
        app = c.app
        with app.state.tasks.transaction() as db:
            import json
            db.execute('UPDATE tasks SET results=? WHERE task_id=?',
                       (json.dumps({'report_ids': ['rpt_A'], 'selected_years': [2024]}), 'task_A'))
        app.state.evidence = EvidenceStore([
            Evidence("evA", "rpt_A", 1, None, "text", "本任务"),
            Evidence("evB", "rpt_B", 1, None, "text", "他任务")])
        seen = {}

        def model(ctx):
            seen["allowed"] = set(ctx["allowed_evidence_ids"])
            return [{"metric_id": "E01", "metric_year": 2024, "status": "extracted",
                     "value": "123", "raw_unit": "tCO2e", "evidence_ids": ["evB"],
                     "extraction_note": "引用他任务证据"}]

        app.state.extraction_deps = ExtractionDeps(
            model_fn=model, extractor_version="selfcheck", model_id=None, prompt_version=None)
        r = c.post("/agent/tool", headers={"x-agent-token": "tok_task_A", "Idempotency-Key": str(uuid4())},
                   json={"tool": "extract_metrics", "arguments": {
                       "metric_ids": ["E01"], "years": [2024], "expected_revision": 7}})
        assert r.status_code == 403, r.text
        assert "evB" not in seen["allowed"], seen["allowed"]
        # 引用任务外证据的候选必须整批拒绝
        assert r.json()["error"]["code"] == "FOREIGN_EVIDENCE"
        assert not app.state.records.list_by_task("task_A")


class TestTaskScope:
    def test_page_evidence_scoped_to_task(self):
        """P1-4: 任务 A 页面不得出现任务 B 的证据原文。"""
        c = _client_with_task("task_A", report_ids=["rpt_A"])
        app = c.app
        app.state.tasks.register_task("task_B", 1, {"report_ids": ["rpt_B"]})
        app.state.evidence = EvidenceStore([
            Evidence("evA", "rpt_A", 1, None, "text", "SECRET_A"),
            Evidence("evB", "rpt_B", 1, None, "text", "SECRET_B")])
        html = c.get("/?task_id=task_A").text
        assert "SECRET_A" in html
        assert "SECRET_B" not in html


class TestScenario:
    def test_zero_factor_is_not_swallowed(self):
        """P1-5: factor=0 不得被 falsy 判断替换成 1。"""
        recs = [{"metric_year": 2024, "value": "100"},
                {"metric_year": 2023, "value": "100"}]
        t = ScenarioTransform("UNIT_CONVERSION",
                              {2024: {"factor": "0"}, 2023: {"factor": "0"}}, "归零假设")
        res = apply_scenario(recs, t)
        assert res["status"] == "ok"
        assert all(r["scenario_value"] == "0" for r in res["records"])


class TestDownloadSession:
    def test_artifact_download_requires_user_session(self):
        """P2: 持有 URL 但无用户会话不得下载。"""
        c = _client_with_task()
        exp = c.post("/tasks/task_sec/exports", headers={"x-user": "u"},
                     json={"expected_revision": 7})
        art = exp.json()["data"]["artifacts"][0]
        assert c.get(art["download_url"]).status_code == 401
        assert c.get(art["download_url"], headers={"x-user": "u"}).status_code == 200

    def test_pdf_requires_user_session(self, tmp_path):
        """P2: PDF 端点同样需要用户会话。"""
        c = _client_with_task(report_ids=["rpt_A"])
        pdf = tmp_path / "sample.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% test\n")
        c.app.state.report_paths = {"rpt_A": pdf}
        assert c.get("/api/reports/rpt_A/pdf").status_code == 401
        assert c.get("/api/reports/rpt_A/pdf",
                     headers={"x-user": "u"}).status_code == 200
