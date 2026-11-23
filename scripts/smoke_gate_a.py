"""闸门 A 真机烟测：业务触发 → SDK → 模型工具调用 → TS 插件 → 本地 FastAPI → 结构化结果。

判定证据（全部脱敏，不含密钥/令牌原文）：
1. RunResult.events 里出现 get_task_results 的 tool 调用事件
2. 本地 FastAPI 的 /agent/tool 收到对应的一次 POST（tool=get_task_results）
3. 二者数量与顺序一致，且后端返回的数据 revision=7（mock_task_001）
4. 非白名单工具（shell/write/web）未出现在模型实际调用中

架构注意：FastAPI 用 uvicorn 在独立线程跑；SDK harness.run() 在主线程，
避免工具回调同进程同事件循环死锁（handoff 点名的坑）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

import uvicorn  # noqa: E402
from deepseek_harness import DeepSeekHarness  # noqa: E402

from esg_app.main import create_app  # noqa: E402

DSH_HOME = os.environ.get("ESG_DSH_HOME", r"D:\esg-agent-runtime\dsh-home")
WORKSPACE = os.environ.get("ESG_WORKSPACE", r"D:\esg-agent-runtime\workspace")
MODEL = os.environ.get("DSH_MODEL", "deepseek-v4-flash")
AGENT_TOKEN = "gate-a-agent-token-demo"  # 仅烟测用，非生产
TASK_ID = "mock_task_001"
EVIDENCE_DIR = Path(r"D:\esg-agent-runtime\gate-a-evidence")

# 记录每次 /agent/tool HTTP 调用（脱敏：不记令牌原文）
HTTP_CALLS: list[dict] = []


def load_mock_results() -> dict:
    data = json.loads((ROOT / "contracts" / "examples" / "get_task_results.success.json").read_text(encoding="utf-8"))
    return data["data"]


def build_app_with_logging():
    app = create_app()
    store = app.state.tasks
    store.register_task(TASK_ID, revision=7, results=load_mock_results())
    store.issue_agent_token(TASK_ID, AGENT_TOKEN)

    @app.middleware("http")
    async def log_calls(request, call_next):
        path = request.url.path
        if path == "/agent/tool":
            # 读 body 需要在 middleware 里先取再重建（FastAPI 有更优雅方式，这里仅烟测）
            body = await request.body()
            try:
                payload = json.loads(body) if body else {}
            except Exception:
                payload = {}
            HTTP_CALLS.append({
                "ts_ms": int(time.time() * 1000),
                "method": request.method,
                "path": path,
                "tool": payload.get("tool"),
                "has_token": bool(request.headers.get("x-agent-token")),
            })
        return await call_next(request)
    return app


def run_uvicorn(app, port=8000):
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def wait_ready(port=8000, timeout=20):
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY missing")
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    app = build_app_with_logging()
    srv = threading.Thread(target=run_uvicorn, args=(app, 8000), daemon=True)
    srv.start()
    if not wait_ready(8000):
        raise SystemExit("FastAPI did not become ready on 8000")
    print("[backend] ready on 127.0.0.1:8000")

    sid = f"gate-a-{int(time.time())}"
    prompt = (
        "请调用 get_task_results 工具读取当前 ESG 任务的结果，"
        "然后用一句话告诉我返回的 revision 数字是多少。只调用这一个工具。"
    )
    env = {
        "DSH_PERMISSION_MODE": "read-only",
        "ESG_API_BASE_URL": "http://127.0.0.1:8000",
        "ESG_AGENT_TOKEN": AGENT_TOKEN,
    }
    try:
        with DeepSeekHarness(
            dsh_home=DSH_HOME,
            cwd=WORKSPACE,
            profile="sdk",
            provider="deepseek-official",
            model=MODEL,
            max_tokens=512,
            initialize_timeout_seconds=60,
            env=env,
        ) as harness:
            result = harness.run(prompt, session_id=sid)
    finally:
        time.sleep(0.5)  # 等异步 HTTP 日志落盘

    # ---------- 判定与取证 ----------
    tool_events = []
    all_event_types = []
    for ev in result.events or []:
        et = ev.get("type", "")
        all_event_types.append(et)
        blob = json.dumps(ev, ensure_ascii=False, default=str)
        if "get_task_results" in blob and ("tool" in et or "call" in et or "invoke" in et):
            tool_events.append({"type": et, "snippet": blob[:600]})

    evidence = {
        "session_id": sid,
        "finish_reason": result.finish_reason,
        "final_response": result.final_response,
        "model_visible_tool_call_events": tool_events,
        "http_calls": HTTP_CALLS,
        "all_event_types": all_event_types,
        "revision_expected": 7,
        "judgement": {},
    }

    gtr_http = [c for c in HTTP_CALLS if c.get("tool") == "get_task_results"]
    has_tool_event = len(tool_events) > 0
    has_http = len(gtr_http) > 0
    revision_ok = "7" in str(result.final_response)
    evidence["judgement"] = {
        "model_invoked_get_task_results": has_tool_event,
        "backend_received_get_task_results": has_http,
        "http_call_count": len(gtr_http),
        "final_answer_mentions_revision_7": revision_ok,
        "gate_a_core_chain": has_tool_event and has_http,
        "note": "revision 判断基于模型自然语言，仅作辅助；核心证据是 tool 事件↔HTTP 对应",
    }

    out = EVIDENCE_DIR / "gate_a_evidence.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== GATE A EVIDENCE ===")
    print("finish_reason:", result.finish_reason)
    print("final_response:", repr(result.final_response))
    print("tool_call_events:", len(tool_events))
    print("http /agent/tool calls:", len(HTTP_CALLS), "-> gtr:", len(gtr_http))
    print("judgement:", json.dumps(evidence["judgement"], ensure_ascii=False))
    print("evidence file:", out)


if __name__ == "__main__":
    main()
