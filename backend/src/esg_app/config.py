"""最小运行配置：服务只绑定本机，工具默认拒绝、显式放行白名单。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    # 默认拒绝：只有显式列入的白名单工具名可被 Agent 调用
    allowed_agent_tools: frozenset[str] = frozenset({"get_task_results", "search_evidence", "extract_metrics", "compare_years"})
    dsh_home: str = ""
    workspace: str = ""
    model: str = ""
