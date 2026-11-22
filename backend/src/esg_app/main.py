"""Task 1 最小 FastAPI 服务。

严格对齐 contracts v0.2.0 的 ToolResult 信封：
- ok=true:  data / error=null / task_id / revision
- ok=false: data=null / error{code,message,retryable} / task_id / revision
- UNAUTHORIZED / FORBIDDEN 时 task_id 与 revision 必须为 null（不泄露任务存在性）
"""
from __future__ import annotations

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .repository import InMemoryTaskStore
from .run_jobs import AgentRunStore
from .retrieval import EvidenceStore, search_evidence as do_search
from .extraction import ExtractionDeps, extract_metrics as do_extract, RevisionChanged


def error_envelope(code: str, message: str, retryable: bool = False) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message, "retryable": retryable},
        "task_id": None,
        "revision": None,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="ESG Agent Business API", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.tasks = InMemoryTaskStore()
    app.state.runs = AgentRunStore()
    # Task 2 证据仓储：默认空，由 register_report/parse 流程填充
    app.state.evidence = EvidenceStore([])
    # Task 3 提取依赖：默认拒绝空候选（模型替身须由后端显式注入）
    app.state.extraction_deps = ExtractionDeps(
        model_fn=lambda ctx: [], extractor_version="backend-default",
        model_id=None, prompt_version=None,
    )

    @app.get("/health")
    def health():
        return {"status": "ok", "contract_version": "0.2.0"}

    def authorized_task(task_id: str, token: str | None) -> str | None:
        store: InMemoryTaskStore = app.state.tasks
        bound = store.task_for_agent_token(token)
        return task_id if bound == task_id else None

    @app.get("/api/tasks/{task_id}/results")
    def get_task_results(task_id: str, x_agent_token: str | None = Header(default=None)):
        if authorized_task(task_id, x_agent_token) is None:
            return JSONResponse(status_code=403,
                                content=error_envelope("FORBIDDEN", "agent token mismatch"))
        store: InMemoryTaskStore = app.state.tasks
        rec = store.get(task_id)
        if rec is None:
            return JSONResponse(status_code=404,
                                content=error_envelope("NOT_FOUND", "task not found"))
        return {
            "ok": True,
            "data": rec.results,
            "error": None,
            "task_id": task_id,
            "revision": rec.revision,
        }

    @app.post("/agent/tool")
    async def agent_tool(request: Request, x_agent_token: str | None = Header(default=None)):
        """Agent 工具统一入口：默认拒绝，只转发契约白名单内的工具名。"""
        store: InMemoryTaskStore = app.state.tasks
        bound = store.task_for_agent_token(x_agent_token)
        if bound is None:
            return JSONResponse(status_code=401,
                                content=error_envelope("UNAUTHORIZED", "missing or invalid agent token"))
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400,
                                content=error_envelope("INVALID_INPUT", "invalid JSON"))
        tool = payload.get("tool")
        args = payload.get("arguments", {})
        if tool not in settings.allowed_agent_tools:
            return JSONResponse(status_code=403,
                                content=error_envelope("FORBIDDEN", "tool not in whitelist"))
        # 用户输入不得夹带原生命令字段；也不得让模型自选 task_id
        if "command" in args or "task_id" in args:
            return JSONResponse(status_code=400,
                                content=error_envelope("INVALID_INPUT", "command or task_id override rejected"))
        if tool == "get_task_results":
            rec = store.get(bound)
            return {
                "ok": True,
                "data": rec.results,
                "error": None,
                "task_id": bound,
                "revision": rec.revision,
            }
        if tool == "search_evidence":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return JSONResponse(status_code=400,
                                    content=error_envelope("INVALID_INPUT", "query required"))
            limit = args.get("limit", 10)
            metric_id = args.get("metric_id")
            evidence_store: EvidenceStore = app.state.evidence
            result = do_search(evidence_store, query,
                               metric_id=metric_id, limit=limit)
            rec = store.get(bound)
            return {
                "ok": True,
                "data": {"evidence": [e.to_dict() for e in result["evidence"]],
                         "issues": result["issues"]},
                "error": None,
                "task_id": bound,
                "revision": rec.revision if rec else 0,
            }
        if tool == "extract_metrics":
            # 输入以服务端任务为准：task_id 来自令牌，revision 来自任务仓储
            metric_ids = args.get("metric_ids")
            years = args.get("years")
            expected_revision = args.get("expected_revision")
            if (not isinstance(metric_ids, list) or not metric_ids
                    or not isinstance(years, list) or not years
                    or not isinstance(expected_revision, int)):
                return JSONResponse(status_code=400,
                                    content=error_envelope("INVALID_INPUT",
                                                           "metric_ids/years/expected_revision required"))
            rec = store.get(bound)
            current_revision = rec.revision if rec else 0
            # 证据归属集合来自服务端证据仓储（模型不能自选）
            evidence_store: EvidenceStore = app.state.evidence
            allowed_evidence_ids = set(evidence_store.by_id.keys())
            deps: ExtractionDeps = app.state.extraction_deps
            try:
                result = do_extract(
                    task_id=bound,
                    expected_revision=expected_revision,
                    current_revision=current_revision,
                    metric_ids=metric_ids, years=years,
                    allowed_evidence_ids=allowed_evidence_ids,
                    deps=deps,
                )
            except RevisionChanged:
                return JSONResponse(status_code=409,
                                    content={**error_envelope("REVISION_CONFLICT",
                                                              "expected_revision does not match current task revision",
                                                              retryable=True),
                                             "task_id": bound, "revision": current_revision})
            return {
                "ok": True,
                "data": result.to_dict(),
                "error": None,
                "task_id": bound,
                "revision": current_revision,
            }
        return JSONResponse(status_code=403,
                            content=error_envelope("FORBIDDEN", "tool not in whitelist"))

    @app.post("/tasks/{task_id}/review")
    def user_review(task_id: str, x_agent_token: str | None = Header(default=None)):
        """人工复核只走用户会话 + CSRF；Agent 令牌无权调用。"""
        if x_agent_token:
            return JSONResponse(status_code=403,
                                content=error_envelope("FORBIDDEN", "agent token cannot review"))
        return JSONResponse(status_code=401,
                            content=error_envelope("UNAUTHORIZED", "user session required"))

    return app
