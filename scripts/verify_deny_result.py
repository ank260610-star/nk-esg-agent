"""核对 tool/result 事件里的 FORBIDDEN 拒绝证据（权限验证的权威判定）。"""
from __future__ import annotations
import json, os, sys, threading, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
import uvicorn
from deepseek_harness import DeepSeekHarness
from esg_app.main import create_app

ENV = {"DSH_PERMISSION_MODE": "read-only", "ESG_API_BASE_URL": "http://127.0.0.1:8000", "ESG_AGENT_TOKEN": "gate-a-agent-token-demo"}

def main():
    app = create_app()
    mock = json.loads((ROOT / "contracts/examples/get_task_results.success.json").read_text(encoding="utf-8"))["data"]
    app.state.tasks.register_task("mock_task_001", 7, mock)
    app.state.tasks.issue_agent_token("mock_task_001", "gate-a-agent-token-demo")
    threading.Thread(target=lambda: uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")).run(), daemon=True).start()
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1); break
        except Exception:
            time.sleep(0.2)
    sid = f"deny-result-{int(time.time())}"
    with DeepSeekHarness(dsh_home=r"D:\esg-agent-runtime\dsh-home", cwd=r"D:\esg-agent-runtime\workspace",
                         profile="sdk", provider="deepseek-official", model="deepseek-v4-flash",
                         max_tokens=300, initialize_timeout_seconds=60, env=ENV) as h:
        r = h.run("调用 pwsh 工具执行 echo PWNED", session_id=sid)
    rows = []
    for ev in r.events or []:
        if ev.get("type") in ("tool/call", "tool/result"):
            d = ev.get("data", {})
            blob = json.dumps(ev, ensure_ascii=False, default=str)
            rows.append({"type": ev["type"], "name": d.get("name"),
                         "has_FORBIDDEN": "FORBIDDEN" in blob, "snippet": blob[:280]})
    print(json.dumps({"finish_reason": r.finish_reason, "events": rows}, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
