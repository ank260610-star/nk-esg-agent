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
