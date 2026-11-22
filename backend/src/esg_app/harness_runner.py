"""DeepSeek Harness Python SDK 宿主薄适配（Task 1）。

真实启动需 deepseek-harness-sdk 已安装且 DEEPSEEK_API_KEY 可用；
本模块只负责参数构造与路径校验，不在导入时启动任何进程。
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import Settings

_FORBIDDEN_WORKSPACE_PARTS = (".ssh", ".aws", ".workbuddy", "AppData")


def ensure_isolated_paths(settings: Settings) -> tuple[str, str]:
    """dsh_home 与 cwd 必须显式且互不落在用户敏感目录。"""
    if not settings.dsh_home or not settings.workspace:
        raise ValueError("dsh_home and workspace must be explicit absolute paths")
    home = Path(settings.dsh_home).resolve()
    workspace = Path(settings.workspace).resolve()
    if home == workspace or home in workspace.parents or workspace in home.parents:
        raise ValueError("dsh_home and workspace must be separate directories")
    lowered = str(workspace).lower()
    if any(part.lower() in lowered for part in _FORBIDDEN_WORKSPACE_PARTS):
        raise ValueError("workspace must not be a home/credential directory")
    return str(home), str(workspace)


def build_sdk_kwargs(settings: Settings) -> dict:
    """返回 DeepSeekHarness 构造参数；模型名由锁定版本实测决定。"""
    home, workspace = ensure_isolated_paths(settings)
    if not settings.model:
        raise ValueError("model must be locked and recorded in docs/runtime-lock.md")
    return {
        "dsh_home": home,
        "cwd": workspace,
        "provider": "deepseek-official",
        "model": settings.model,
        "reasoning_effort": "max",
        "max_tokens": 49_152,
        "initialize_timeout_seconds": 30,
    }


def run_agent(settings: Settings, prompt: str, session_id: str):
    """真实运行入口：缺密钥时明确失败，不返回模拟结果。"""
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY missing; real Harness run not attempted")
    from deepseek_harness import DeepSeekHarness  # 延迟导入，离线测试不需要 SDK

    with DeepSeekHarness(**build_sdk_kwargs(settings)) as harness:
        return harness.run(prompt, session_id=session_id)
