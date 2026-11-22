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
