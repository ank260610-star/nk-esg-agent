"""闸门 A 诊断：完整 dump 模型可见的工具清单与权限上下文。

不触发工具调用，只读 RunResult.events，用于判断：
- 插件是否被加载（get_task_results 是否出现在工具清单）
- read-only 权限预设是否生效
- 非白名单工具（shell/pwsh/web/fs_write）是否对模型可见
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepseek_harness import DeepSeekHarness  # noqa: E402

DSH_HOME = os.environ.get("ESG_DSH_HOME", r"D:\esg-agent-runtime\dsh-home")
WORKSPACE = os.environ.get("ESG_WORKSPACE", r"D:\esg-agent-runtime\workspace")
MODEL = os.environ.get("DSH_MODEL", "deepseek-v4-flash")


def find_tool_names(obj, acc):
    """递归找出所有看起来像工具定义里的 name 字段。"""
    if isinstance(obj, dict):
        # 工具对象典型形状：{name, description, parameters/inputSchema}
        if "name" in obj and ("description" in obj or "parameters" in obj or "inputSchema" in obj):
            acc.add(str(obj["name"]))
        for v in obj.values():
            find_tool_names(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            find_tool_names(v, acc)


def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY missing")
    sid = f"probe-tools-{int(time.time())}"
    with DeepSeekHarness(
        dsh_home=DSH_HOME,
        cwd=WORKSPACE,
        profile="sdk",
        provider="deepseek-official",
        model=MODEL,
        max_tokens=64,
        initialize_timeout_seconds=60,
        env={"DSH_PERMISSION_MODE": "read-only"},
    ) as harness:
        result = harness.run("回复 OK", session_id=sid)

    print("finish_reason:", result.finish_reason)
    names = set()
    for ev in result.events or []:
        et = ev.get("type", "")
        if et in ("request/header", "request/context"):
            payload = json.dumps(ev, ensure_ascii=False, default=str)
            # 保存到文件供逐字核对
            with open(f"D:/esg-agent-runtime/probe_{et.replace('/', '_')}.json", "w", encoding="utf-8") as f:
                f.write(payload)
            local = set()
            find_tool_names(ev, local)
            print(f"EVENT {et}: payload_len={len(payload)} candidate_tool_names={sorted(local)}")
            names |= local
    print("=== ALL CANDIDATE TOOL NAMES ===")
    for n in sorted(names):
        print(" -", n)
    print("=== get_task_results visible? ===", "get_task_results" in names)


if __name__ == "__main__":
    main()
