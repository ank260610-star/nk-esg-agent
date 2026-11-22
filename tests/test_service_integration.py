"""Task 2 服务端集成：search_evidence 通过 /agent/tool 可达且信封合规。"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from fastapi.testclient import TestClient
from esg_app.config import Settings
from esg_app.main import create_app
from esg_app.retrieval import Evidence, EvidenceStore
from esg_app.repository import InMemoryTaskStore


def ev(eid, text, page=127):
    return Evidence(eid, "r1", page, "126", "table", text,
                    viewer_url=f"/api/reports/r1/pdf#page={page}")


def build_client(with_evidence=True):
    settings = Settings()
    app = create_app(settings)
    # 注册一个 mock task + agent token（与闸门 A 同机制）
    store: InMemoryTaskStore = app.state.tasks
    store.register_task("task_svc", 7, {})
    store.issue_agent_token("task_svc", "tok_svc")
    if with_evidence:
        app.state.evidence = EvidenceStore([
            ev("e_e01", "温室气体排放量 12,341,455.00"),
            ev("e_e03", "范围3温室气体排放量 3,326,312.49"),
        ])
    return TestClient(app)


class TestSearchToolIntegration:
    def test_search_evidence_reachable_via_agent_tool(self):
        c = build_client()
        r = c.post("/agent/tool",
                   headers={"x-agent-token": "tok_svc"},
                   json={"tool": "search_evidence",
                         "arguments": {"query": "温室气体排放量"}})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["task_id"] == "task_svc"
        assert "evidence" in body["data"]
        assert len(body["data"]["evidence"]) >= 1

    def test_search_requires_token(self):
        c = build_client()
        r = c.post("/agent/tool",
                   json={"tool": "search_evidence",
                         "arguments": {"query": "温室气体"}})
        assert r.status_code == 401

    def test_arguments_cannot_override_task_id(self):
        c = build_client()
        r = c.post("/agent/tool",
                   headers={"x-agent-token": "tok_svc"},
                   json={"tool": "search_evidence",
                         "arguments": {"query": "温室气体", "task_id": "other"}})
        assert r.status_code == 400


class TestExtractToolIntegration:
    def test_extract_metrics_reachable_with_stub_model(self):
        """extract_metrics 通过 /agent/tool 可达；用后端注入的替身模型。"""
        from esg_app.extraction import ExtractionDeps

        settings = Settings()
        app = create_app(settings)
        store: InMemoryTaskStore = app.state.tasks
        store.register_task("task_ext", 3, {})
        store.issue_agent_token("task_ext", "tok_ext")
        app.state.evidence = EvidenceStore([
            ev("e_e01", "范围1温室气体排放量 12,341,455.00"),
        ])

        def stub_model(ctx):
            return [{
                "report_id": "byd_report", "metric_id": "E01", "metric_year": 2024,
                "raw_value": "12,341,455.00", "value": "12341455",
                "raw_unit": "吨二氧化碳当量", "canonical_unit": "吨二氧化碳当量",
                "conversion_rule": None, "denominator": None,
                "boundary_text": "", "boundary_key": None, "method_text": "",
                "scope3_categories": [], "evidence_ids": ["e_e01"],
                "status": "extracted", "origin": "reported", "supersedes_id": None,
                "derivation": None, "extraction_note": "",
            }]

        app.state.extraction_deps = ExtractionDeps(
            model_fn=stub_model, extractor_version="test-v1",
            model_id="stub", prompt_version="metric-extraction-v0.2.0",
        )
        c = TestClient(app)
        r = c.post("/agent/tool",
                   headers={"x-agent-token": "tok_ext"},
                   json={"tool": "extract_metrics",
                         "arguments": {"metric_ids": ["E01"], "years": [2024],
                                       "expected_revision": 3}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["task_id"] == "task_ext"
        assert "records" in body["data"]
        assert len(body["data"]["records"]) >= 1
        # 服务端字段必须存在
        rec = body["data"]["records"][0]
        assert {"record_id", "task_id", "revision", "review_state"} <= set(rec)

    def test_extract_rejects_wrong_revision(self):
        """expected_revision 与服务端不符 → 拒绝。"""
        from esg_app.extraction import ExtractionDeps

        settings = Settings()
        app = create_app(settings)
        store: InMemoryTaskStore = app.state.tasks
        store.register_task("task_ext2", 5, {})
        store.issue_agent_token("task_ext2", "tok_ext2")
        app.state.extraction_deps = ExtractionDeps(
            model_fn=lambda ctx: [], extractor_version="test-v1",
            model_id="stub", prompt_version="v1",
        )
        c = TestClient(app)
        r = c.post("/agent/tool",
                   headers={"x-agent-token": "tok_ext2"},
                   json={"tool": "extract_metrics",
                         "arguments": {"metric_ids": ["E01"], "years": [2024],
                                       "expected_revision": 4}})  # 实际是 5
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "REVISION_CONFLICT"


class TestCompareToolIntegration:
    def _record(self, rid, year, value):
        return {
            "report_id": "byd_r1", "metric_id": "E04", "metric_year": year,
            "raw_value": value, "value": value, "raw_unit": "MWh",
            "canonical_unit": "MWh", "conversion_rule": None, "denominator": None,
            "boundary_text": "", "boundary_key": "scope_cn",
            "method_text": "已核对口径", "scope3_categories": [],
            "evidence_ids": [], "status": "extracted", "origin": "reported",
            "supersedes_id": None, "derivation": None, "record_id": rid,
            "task_id": "task_cmp", "review_state": "confirmed", "revision": 3,
            "is_current": True, "extraction_note": "",
        }

    def test_compare_years_reachable_and_computes_yoy(self):
        settings = Settings()
        app = create_app(settings)
        store: InMemoryTaskStore = app.state.tasks
        store.register_task("task_cmp", 3, {})
        store.issue_agent_token("task_cmp", "tok_cmp")
        app.state.records.add("r_old", self._record("r_old", 2023, "16592592.44"))
        app.state.records.add("r_new", self._record("r_new", 2024, "23552569.28"))
        c = TestClient(app)
        r = c.post("/agent/tool", headers={"x-agent-token": "tok_cmp"},
                   json={"tool": "compare_years",
                         "arguments": {"older_record_id": "r_old",
                                       "newer_record_id": "r_new",
                                       "expected_revision": 3}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["data"]["status"] == "comparable"
        assert body["data"]["percent_change"] == "41.95"

    def test_compare_with_foreign_record_rejected(self):
        """跨任务 record_id → 402/403 拒绝，不泄露。"""
        settings = Settings()
        app = create_app(settings)
        store: InMemoryTaskStore = app.state.tasks
        store.register_task("task_cmp2", 3, {})
        store.issue_agent_token("task_cmp2", "tok_cmp2")
        # r_old 属于别的 task
        rec = self._record("r_foreign", 2023, "100"); rec["task_id"] = "other_task"
        app.state.records.add("r_foreign", rec)
        c = TestClient(app)
        r = c.post("/agent/tool", headers={"x-agent-token": "tok_cmp2"},
                   json={"tool": "compare_years",
                         "arguments": {"older_record_id": "r_foreign",
                                       "newer_record_id": "r_missing",
                                       "expected_revision": 3}})
        assert r.status_code in (403, 404)


class TestScoreToolIntegration:
    """Task 5: calculate_score / get_rulebook 走 /agent/tool；复核走独立用户会话。"""

    def _all_unknown_checks(self):
        ids = ["C01", "C02", "C03", "C04", "Q01", "Q02", "Q03", "Q04",
               "B01", "B02", "B03", "B04", "Y01", "Y02", "Y03", "Y04",
               "T01", "T02", "T03", "T04"]
        return [
            {"check_id": cid, "rule_version": "disclosure-v0.1", "status": "unknown",
             "evidence_ids": [], "reason": "待复核", "missing_information": [],
             "suggestion": "", "target_key": None, "task_id": "task_svc",
             "origin": "model", "review_state": "unreviewed", "revision": 7, "is_current": True}
            for cid in ids
        ]

    def test_get_rulebook_reachable_via_agent_tool(self):
        c = build_client()
        r = c.post("/agent/tool", headers={"x-agent-token": "tok_svc"},
                   json={"tool": "get_rulebook", "arguments": {}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["data"]["experimental"] is True
        assert len(body["data"]["checks"]) == 20

    def test_calculate_score_hand_case_via_agent_tool(self):
        c = build_client()
        checks = self._all_unknown_checks()
        # C01=met, C02=partial
        for ch in checks:
            if ch["check_id"] == "C01":
                ch.update(status="met", origin="manual", review_state="confirmed",
                          evidence_ids=["ev_e01"])
            if ch["check_id"] == "C02":
                ch.update(status="partial", origin="manual", review_state="confirmed",
                          evidence_ids=["ev_e03"])
        app = c.app
        app.state.checks = {"task_svc": checks}
        r = c.post("/agent/tool", headers={"x-agent-token": "tok_svc"},
                   json={"tool": "calculate_score", "arguments": {}})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["coverage"] == "0.125"
        assert d["reference_score"] == "75"
        assert d["lower_bound"] == "9.375"
        assert d["upper_bound"] == "96.875"
        assert d["final_score"] is None
        assert d["label"] == "insufficient_evidence"

    def test_review_rejects_agent_token(self):
        c = build_client()
        r = c.post("/tasks/task_svc/review", headers={"x-agent-token": "tok_svc"},
                   json={"action": "set_check_status", "check_id": "C01",
                         "new_status": "met", "reason": "已核对", "expected_revision": 7})
        assert r.status_code == 403
