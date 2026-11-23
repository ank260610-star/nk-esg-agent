"""闸门 A STEP 1：最小真实 LLM 调用（不带插件/工具）。

验证：密钥有效、模型 ID 可用、provider 路由通、SDK 能启动 runtime。
不做任何工具调用，不作为闸门 A 通过证据。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepseek_harness import DeepSeekHarness  # noqa: E402

logging.basicConfig(level=logging.WARNING)

DSH_HOME = os.environ.get("ESG_DSH_HOME", r"D:\esg-agent-runtime\dsh-home")
WORKSPACE = os.environ.get("ESG_WORKSPACE", r"D:\esg-agent-runtime\workspace")
MODEL = os.environ.get("DSH_MODEL", "deepseek-v4-flash")


def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY missing")
    with DeepSeekHarness(
        dsh_home=DSH_HOME,
        cwd=WORKSPACE,
        profile="sdk",
        provider="deepseek-official",
        model=MODEL,
        max_tokens=256,
        initialize_timeout_seconds=60,
        env={"DSH_PERMISSION_MODE": "read-only"},
    ) as harness:
        import time
        result = harness.run("只回复这两个字：连通", session_id=f"smoke-llm-{int(time.time())}")
    print("finish_reason:", result.finish_reason)
    print("final_response:", repr(result.final_response))
    print("session_id:", result.session_id)
    print("events_count:", len(result.events or []))
    import json
    for ev in result.events or []:
        print("EVENT:", json.dumps(ev, ensure_ascii=False, default=str)[:1200])


if __name__ == "__main__":
    main()
