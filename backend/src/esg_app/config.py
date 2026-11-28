"""最小运行配置：服务只绑定本机，工具默认拒绝、显式放行白名单。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    public_mode: bool = False
    public_origin: str = ""
    proxy_secret: str = ""
    max_request_bytes: int = 1_048_576
    requests_per_minute: int = 120
    host: str = "127.0.0.1"
    port: int = 8000
    # 默认拒绝：只有显式列入的白名单工具名可被 Agent 调用
    allowed_agent_tools: frozenset[str] = frozenset(
        {
            "parse_report",
            "get_task_results",
            "search_evidence",
            "extract_metrics",
            "compare_years",
            "get_rulebook",
            "calculate_score",
            "save_check_proposals",
            "validate_metrics",
            "export_analysis",
        }
    )
    max_extraction_model_calls: int = 12
    database_path: str = ":memory:"
    dsh_home: str = ""
    workspace: str = ""
    model: str = ""
    harness_enabled: bool = False
    harness_patch: str = ""
    agent_api_base_url: str = "http://127.0.0.1:8000"
    extraction_model: str = ""
    extraction_timeout: int = 100
    extraction_max_tokens: int = 16384

    @classmethod
    def from_env(cls):
        import os

        return cls(
            public_mode=os.environ.get("ESG_PUBLIC_MODE", "0") == "1",
            public_origin=os.environ.get("ESG_PUBLIC_ORIGIN", ""),
            proxy_secret=os.environ.get("ESG_PROXY_SECRET", ""),
            max_request_bytes=int(os.environ.get("ESG_MAX_REQUEST_BYTES", "1048576")),
            requests_per_minute=int(os.environ.get("ESG_REQUESTS_PER_MINUTE", "120")),
            harness_enabled=os.environ.get("ESG_HARNESS_ENABLED", "0") == "1",
            harness_patch=os.environ.get("ESG_HARNESS_PATCH", ""),
            agent_api_base_url=os.environ.get(
                "ESG_API_BASE_URL", "http://127.0.0.1:8000"
            ),
            database_path=os.environ.get("ESG_DATABASE_PATH", "data/esg.sqlite3"),
            extraction_model=os.environ.get("ESG_EXTRACTION_MODEL", ""),
            extraction_timeout=int(os.environ.get("ESG_EXTRACTION_TIMEOUT", "100")),
            extraction_max_tokens=int(
                os.environ.get("ESG_EXTRACTION_MAX_TOKENS", "16384")
            ),
            dsh_home=os.environ.get("ESG_DSH_HOME", ""),
            workspace=os.environ.get("ESG_WORKSPACE", ""),
            model=os.environ.get("DSH_MODEL", ""),
        )
