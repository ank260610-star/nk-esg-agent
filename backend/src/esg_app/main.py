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
