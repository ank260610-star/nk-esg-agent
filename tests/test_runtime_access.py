"""闸门 A 应用级测试：权限隔离、工具白名单、宿主状态映射。

测试替身与模拟数据仅用于离线验证；不代表真实 Harness 工具调用通过。
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from esg_app.config import Settings  # noqa: E402
from esg_app.main import create_app  # noqa: E402
from esg_app.repository import InMemoryTaskStore  # noqa: E402
from esg_app.run_jobs import AgentRunStore, RunStatus  # noqa: E402


SAMPLE = json.loads(
    (ROOT / "contracts" / "examples" / "get_task_results.success.json").read_text(encoding="utf-8")
)


def make_client():
    store = InMemoryTaskStore()
    store.register_task("task_A", revision=7, results=SAMPLE["data"])
    store.register_task("task_B", revision=3, results=SAMPLE["data"])
    store.issue_agent_token("task_A", "token_A")
    store.issue_agent_token("task_B", "token_B")
    app = create_app(Settings(allowed_agent_tools=frozenset({"get_task_results"})))
    app.state.tasks = store
    app.state.runs = AgentRunStore()
    return TestClient(app), store


def test_agent_token_for_other_task_returns_403_without_leaking_revision():
    client, _ = make_client()
    r = client.get("/api/tasks/task_B/results", headers={"X-Agent-Token": "token_A"})
    assert r.status_code == 403
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["task_id"] is None
    assert body["revision"] is None


def test_agent_token_cannot_use_user_review_route():
    client, _ = make_client()
    r = client.post("/tasks/task_A/review", headers={"X-Agent-Token": "token_A"}, json={})
    assert r.status_code in (401, 403)
    assert r.json().get("error", {}).get("code") in ("UNAUTHORIZED", "FORBIDDEN")


def test_non_whitelisted_tool_is_rejected():
    client, _ = make_client()
    r = client.post("/agent/tool", headers={"X-Agent-Token": "token_A"},
                    json={"tool": "save_metric_candidates", "arguments": {}})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_slash_command_does_not_change_permission():
    client, store = make_client()
    r = client.post("/agent/tool", headers={"X-Agent-Token": "token_A"},
                    json={"tool": "get_task_results", "arguments": {
                        "task_id": "task_A", "command": "/permission danger-full-access"}})
    assert r.status_code in (400, 403)
    assert store.permission_for("task_A") != "danger-full-access"


def test_whitelisted_tool_returns_contract_envelope():
    client, _ = make_client()
    r = client.get("/api/tasks/task_A/results", headers={"X-Agent-Token": "token_A"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["task_id"] == "task_A"
    assert body["revision"] == 7
    assert body["data"]["task"]["revision"] == 7


def test_sdk_failure_is_not_marked_completed():
    runs = AgentRunStore()
    run = runs.start("task_A", "session-1")
    runs.fail(run.run_id, "SDK_DISCONNECTED")
    assert runs.get(run.run_id).status is RunStatus.FAILED
    assert runs.get(run.run_id).error


def test_agent_run_does_not_block_http_service():
    """SDK 后台执行期间，同一 HTTP 服务仍可响应。"""
    client, _ = make_client()
    holder = {}

    def fake_blocking():
        holder["started"] = True
        time.sleep(0.6)
        holder["done"] = True

    t = threading.Thread(target=fake_blocking, daemon=True)
    t.start()
    time.sleep(0.05)
    t0 = time.monotonic()
    r = client.get("/health")
    elapsed = time.monotonic() - t0
    t.join()
    assert r.status_code == 200
    assert elapsed < 0.3, f"health blocked {elapsed:.2f}s by background work"
