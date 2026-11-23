"""SDK 宿主适配测试：锁定参数、隔离 home/cwd、恢复会话权限重检。

注意：本文件只测宿主构造逻辑，不发起真实 SDK 进程或模型请求。
真实工具调用因缺 DEEPSEEK_API_KEY 未验证，见 docs/decision-log.md。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from esg_app.config import Settings  # noqa: E402
from esg_app.harness_runner import build_sdk_kwargs, ensure_isolated_paths  # noqa: E402
from esg_app.run_jobs import AgentRunStore, RunStatus  # noqa: E402


def test_sdk_kwargs_never_falls_back_to_home_dsh(tmp_path):
    settings = Settings(dsh_home=str(tmp_path / "dsh-home"),
                        workspace=str(tmp_path / "workspace"),
                        model="deepseek-flash")
    kwargs = build_sdk_kwargs(settings)
    assert kwargs["dsh_home"] == settings.dsh_home
    assert kwargs["cwd"] == settings.workspace
    assert kwargs["model"] == "deepseek-flash"
    assert "provider" in kwargs and kwargs["provider"] == "deepseek-official"


def test_missing_isolated_paths_rejected():
    with pytest.raises(ValueError):
        ensure_isolated_paths(Settings(dsh_home="", workspace=""))


def test_workspace_must_not_be_home_or_key_dir():
    with pytest.raises(ValueError):
        ensure_isolated_paths(Settings(dsh_home=r"C:\Users\DELL",
                                       workspace=r"C:\Users\DELL\.ssh"))


def test_no_empty_final_response_is_completed():
    runs = AgentRunStore()
    run = runs.start("task_A", "s1")
    with pytest.raises(ValueError):
        runs.complete(run.run_id, "", 7)
    assert runs.get(run.run_id).status is RunStatus.RUNNING
