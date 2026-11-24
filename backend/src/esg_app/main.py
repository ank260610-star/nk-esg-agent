"""ESG 业务 FastAPI 服务。

严格对齐 contracts v0.2.0 的 ToolResult 信封：
- ok=true:  data / error=null / task_id / revision
- ok=false: data=null / error{code,message,retryable} / task_id / revision
- UNAUTHORIZED / FORBIDDEN 时 task_id 与 revision 必须为 null（不泄露任务存在性）
"""
from __future__ import annotations

import os
import json
from uuid import uuid4
from copy import deepcopy
import re
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from starlette.concurrency import run_in_threadpool

from .config import Settings
from .parse_repository import ParseRepository, read_business, save_business, bound_reports, token_identity
from .parse_service import ParseService, ParseError
from .retrieval import Evidence
from .repository import InMemoryTaskStore, PersistentRecordStore
from .run_jobs import AgentRunStore
from .retrieval import EvidenceStore, search_evidence as do_search
from .extraction import ExtractionDeps
from .scoring import load_rulebook, score_checks as do_score
from .review import ReviewStore, ReviewCommand, apply_review as do_review, ReviewError
from .exports import ArtifactStore
from .export_service import ExportService
from .rules_registry import ALL_CHECK_IDS
from .write_service import WriteService, WRITES, envelope, validate


def error_envelope(code: str, message: str, retryable: bool = False) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message, "retryable": retryable},
        "task_id": None,
        "revision": None,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="ESG Agent Business API", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.tasks = ParseRepository(settings.database_path)
    app.state.runs = AgentRunStore(app.state.tasks)
    # Task 4 记录仓储：extract 产出按 record_id 存，供 compare 取用
    app.state.records = PersistentRecordStore(app.state.tasks)
    # Task 2 证据仓储：默认空，由 register_report/parse 流程填充
    app.state.evidence = EvidenceStore([])
    # Task 3 提取依赖：默认拒绝空候选（模型替身须由后端显式注入）
    app.state.extraction_deps = ExtractionDeps(
        model_fn=None, extractor_version="backend-default",
        model_id=None, prompt_version=None,
    )
    # 检查项以 SQLite 业务快照为准；映射保留给可信宿主的演示夹具。
    app.state.rulebook = load_rulebook()
    app.state.checks: dict[str, list] = {}
    app.state.review_store = ReviewStore()
    # Task 6 导出：产物目录由服务端生成，下载仅按 artifact_id 查表
    # 文件数据库的产物放在同一数据目录；内存配置使用运行目录的 data/。
    data_dir = Path(settings.database_path).parent if settings.database_path != ":memory:" else Path("data")
    app.state.artifacts = ArtifactStore(data_dir / "artifacts", app.state.tasks)
    app.state.exports_by_task: dict[str, dict] = {}
    # 已登记报告的真实文件路径只存在服务端映射，页面只得到 report_id
    app.state.report_paths: dict[str, Path] = {}
    app.state.report_meta: dict[str, dict] = {}
    app.state.parse_service = ParseService(app.state.tasks, app.state.report_paths)
    # 后台运行：真实的 SDK 宿主由 harness_runner 提供；缺密钥则标记 failed
    app.state.agent_starter = None  # 由测试/运行器注入 (task_id, run_id, mode) -> None
    app.state.comparisons_by_task: dict[str, list] = {}

    # Restore persisted business views for the existing page adapters.
    with app.state.tasks.transaction() as db:
        for (tid,) in db.execute('SELECT task_id FROM business_state'):
            state = read_business(db, tid)
            app.state.checks[tid] = state['checks']
            app.state.comparisons_by_task[tid] = state['comparisons']
            review = state.get('review')
            if review:
                app.state.review_store._tasks[tid] = review['task']
                app.state.review_store._checks[tid] = {c['check_id']: c for c in state['checks']}
                app.state.review_store._events[tid] = review['events']

    if settings.harness_enabled:
        from .agent_host import make_agent_starter
        app.state.agent_starter = make_agent_starter(app)

    _pkg = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(_pkg / "templates"))
    app.mount("/static", StaticFiles(directory=str(_pkg / "static")), name="static")

    @app.get("/health")
    def health():
        return {"status": "ok", "contract_version": "0.2.0"}

    def authorized_task(task_id: str, token: str | None) -> str | None:
        store: InMemoryTaskStore = app.state.tasks
        bound = store.task_for_agent_token(token)
        return task_id if bound == task_id else None

    def task_evidence(task_id: str) -> list:
        """只返回当前任务已登记报告范围内的证据，防止跨任务内容泄露。"""
        rec = app.state.tasks.get(task_id)
        if rec is None:
            return []
        results = rec.results or {}
        allowed = set(bound_reports(results))
        parsed = [Evidence(**e) for e in app.state.tasks.evidence_for_task(task_id)]
        parsed_reports = {e.report_id for e in parsed}
        return parsed + [e for e in app.state.evidence.items
                         if e.report_id in allowed and e.report_id not in parsed_reports]

    app.state.write_service = WriteService(app, task_evidence)
    app.state.export_service = ExportService(app, task_evidence)
    if settings.extraction_model and os.environ.get('DEEPSEEK_API_KEY', '').strip():
        from .model_extractor import DeepSeekExtractor, EXTRACTOR_VERSION, PROMPT_VERSION
        app.state.extraction_deps = ExtractionDeps(
            DeepSeekExtractor(task_evidence, settings.extraction_model, settings.extraction_timeout, settings.extraction_max_tokens),
            EXTRACTOR_VERSION, settings.extraction_model, PROMPT_VERSION)

    def current_results(task_id, token):
        rec = app.state.tasks.get(task_id)
        data = rec.results
        if not hasattr(app.state.tasks, 'transaction'):
            return envelope(task_id, rec.revision, data)
        with app.state.tasks.transaction() as db:
            state = read_business(db, task_id)
            revision, data = app.state.write_service.auth(db, task_id, token)
            parsed = {rid:(digest,version,json.loads(summary)) for rid,digest,version,summary in db.execute('SELECT report_id,hash,version,summary FROM task_parses WHERE task_id=?',(task_id,))}
            exported = any(json.loads(raw)['revision']==revision for (raw,) in db.execute('SELECT metadata FROM artifacts WHERE task_id=?',(task_id,)))
        data.update(metrics=state['records'], checks=state['checks'], comparisons=state['comparisons'],
                    extraction_runs=state['runs'], issues=state['issues'], score=state.get('score') if state.get('score', {}).get('revision') == revision else None,
                    review_url=f'/tasks/{task_id}/review')
        if isinstance(data.get('task'), dict):
            data['task']['revision'] = revision
            if exported:
                data['task']['stage'] = 'exported'
            elif state.get('review',{}).get('task',{}).get('revision') == revision:
                data['task']['stage'] = 'review'
            elif any(c.get('is_current') for c in state['checks']):
                data['task']['stage'] = 'checks'
            elif any(r.get('is_current') for r in state['records']):
                data['task']['stage'] = 'metrics'
            elif parsed:
                data['task']['stage'] = 'parsed'
        for report in data.get('reports',[]):
            if report['report_id'] in parsed:
                digest,version,summary = parsed[report['report_id']]
                report.update(sha256=digest,parser_version=version,page_count=summary['page_count'],status=summary['status'],issues=summary['issues'])
        # Host registration may contain private fields; expose only the domain result.
        data = {k: v for k, v in data.items() if k in ('task', 'reports', 'metrics', 'checks', 'comparisons', 'score', 'issues', 'review_url', 'extraction_runs')}
        if not validate('TaskResults', data):
            return JSONResponse(status_code=422, content={**error_envelope('INVALID_STATE', 'Task metadata is incomplete; host registration is required'), 'task_id': task_id, 'revision': revision})
        return envelope(task_id, revision, data)

    async def execute_write(task_id, token, tool, key, body):
        try:
            return await run_in_threadpool(app.state.write_service.run, task_id, token, tool, key, body)
        except ParseError as exc:
            return JSONResponse(status_code=exc.status, content=exc.payload)
        except Exception:
            return JSONResponse(status_code=500, content=error_envelope('INTERNAL_ERROR', 'Business operation failed'))

    async def execute_read(task_id, token, tool, body):
        try:
            return await run_in_threadpool(app.state.write_service.read, task_id, token, tool, body)
        except ParseError as exc:
            return JSONResponse(status_code=exc.status, content=exc.payload)
        except Exception:
            return JSONResponse(status_code=500, content=error_envelope('INTERNAL_ERROR', 'Read operation failed'))

    @app.get('/api/tasks/{task_id}/score')
    @app.get('/api/tasks/{task_id}/rulebook')
    @app.post('/api/tasks/{task_id}/validate')
    async def read_route(task_id: str, request: Request):
        authorization = request.headers.get('authorization', '')
        token = authorization[7:] if authorization.lower().startswith('bearer ') else None
        endpoint = request.url.path.rsplit('/', 1)[1]
        try:
            body = await request.json() if endpoint == 'validate' else {}
        except Exception:
            body = None
        tool = {'score': 'calculate_score', 'rulebook': 'get_rulebook', 'validate': 'validate_metrics'}[endpoint]
        return await execute_read(task_id, token, tool, body)

    @app.post('/api/tasks/{task_id}/exports')
    @app.post('/api/tasks/{task_id}/extract')
    @app.post('/api/tasks/{task_id}/compare')
    @app.post('/api/tasks/{task_id}/checks')
    async def write_route(task_id: str, request: Request):
        authorization = request.headers.get('authorization', '')
        token = authorization[7:] if authorization.lower().startswith('bearer ') else None
        tool = {'extract': 'extract_metrics', 'compare': 'compare_years', 'checks': 'save_check_proposals', 'exports': 'export_analysis'}[request.url.path.rsplit('/', 1)[1]]
        try:
            body = await request.json()
        except Exception:
            body = None
        return await execute_write(task_id, token, tool, request.headers.get('idempotency-key'), body)

    @app.get("/api/tasks/{task_id}/results")
    def get_task_results(task_id: str, request: Request, x_agent_token: str | None = Header(default=None)):
        authorization = request.headers.get('authorization', '')
        if authorization.lower().startswith('bearer '):
            x_agent_token = authorization[7:]
        if authorized_task(task_id, x_agent_token) is None:
            return JSONResponse(status_code=403,
                                content=error_envelope("FORBIDDEN", "agent token mismatch"))
        store: InMemoryTaskStore = app.state.tasks
        rec = store.get(task_id)
        if rec is None:
            return JSONResponse(status_code=404,
                                content=error_envelope("NOT_FOUND", "task not found"))
        try:
            return current_results(task_id, x_agent_token)
        except ParseError as exc:
            return JSONResponse(status_code=exc.status, content=exc.payload)

    async def execute_parse(task_id, token, key, body):
        try:
            return await run_in_threadpool(app.state.parse_service.run, task_id, token, key, body)
        except ParseError as exc:
            return JSONResponse(status_code=exc.status, content=exc.payload)
        except Exception:
            return JSONResponse(status_code=500, content=error_envelope("INTERNAL_ERROR", "Parse operation failed"))

    @app.post("/api/tasks/{task_id}/parse")
    async def parse_report(task_id: str, request: Request):
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.lower().startswith("bearer ") else None
        try:
            body = await request.json()
        except Exception:
            body = None
        return await execute_parse(task_id, token, request.headers.get("idempotency-key"), body)

    async def dispatch_agent_tool(request: Request, x_agent_token: str | None = Header(default=None)):
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
        # 入口类型守门：模型/用户传来的顶层与参数都必须是对象，否则下游 .get 会 500
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400,
                                content=error_envelope("INVALID_INPUT", "payload must be an object"))
        tool = payload.get("tool")
        request.state.agent_tool_name = tool if isinstance(tool,str) else "invalid"
        args = payload.get("arguments", {})
        if not isinstance(args, dict):
            return JSONResponse(status_code=400,
                                content=error_envelope("INVALID_INPUT", "arguments must be an object"))
        if not isinstance(tool, str):
            return JSONResponse(status_code=422, content=error_envelope('INVALID_INPUT', 'Tool must be a string'))
        if tool not in settings.allowed_agent_tools:
            return JSONResponse(status_code=403,
                                content=error_envelope("FORBIDDEN", "tool not in whitelist"))
        # 用户输入不得夹带原生命令字段；也不得让模型自选 task_id
        if "command" in args or "task_id" in args:
            return JSONResponse(status_code=400,
                                content=error_envelope("INVALID_INPUT", "command or task_id override rejected"))
        if tool == "parse_report":
            return await execute_parse(bound, x_agent_token, request.headers.get("idempotency-key"), args)
        if tool in WRITES:
            return await execute_write(bound, x_agent_token, tool, request.headers.get('idempotency-key'), args)
        if tool == "get_task_results":
            if args:
                return JSONResponse(status_code=422, content=error_envelope('INVALID_INPUT', 'No arguments allowed'))
            try:
                return current_results(bound, x_agent_token)
            except ParseError as exc:
                return JSONResponse(status_code=exc.status, content=exc.payload)
        if tool == "search_evidence":
            if not validate('search_evidence_input', dict(args, task_id=bound)):
                return JSONResponse(status_code=422, content=error_envelope('INVALID_INPUT', 'Invalid search arguments'))
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return JSONResponse(status_code=400,
                                    content=error_envelope("INVALID_INPUT", "query required"))
            limit = args.get("limit", 10)
            # bool 是 int 子类，必须排除；上限防止大结果集拖垮工具入口
            if (not isinstance(limit, int) or isinstance(limit, bool)
                    or limit < 1 or limit > 20):
                return JSONResponse(status_code=400,
                                    content=error_envelope("INVALID_INPUT",
                                                           "limit must be an integer 1..20"))
            metric_id = args.get("metric_id")
            if metric_id is not None and not isinstance(metric_id, str):
                return JSONResponse(status_code=400,
                                    content=error_envelope("INVALID_INPUT",
                                                           "metric_id must be a string"))
            # 检索只在令牌绑定任务的报告范围内进行
            evidence_store: EvidenceStore = EvidenceStore(task_evidence(bound))
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
        if tool in {'get_rulebook', 'calculate_score', 'validate_metrics'}:
            return await execute_read(bound, x_agent_token, tool, args)
        return JSONResponse(status_code=403,
                            content=error_envelope("FORBIDDEN", "tool not in whitelist"))

    @app.post('/agent/tool')
    async def agent_tool(request: Request, x_agent_token: str | None = Header(default=None)):
        response = await dispatch_agent_tool(request, x_agent_token)
        bound = app.state.tasks.task_for_agent_token(x_agent_token)
        if bound and hasattr(app.state.tasks,'transaction'):
            import json
            payload = json.loads(response.body) if isinstance(response,JSONResponse) else response
            tool = getattr(request.state,'agent_tool_name','invalid')
            with app.state.tasks.transaction() as db:
                db.execute('INSERT INTO agent_tool_calls(identity,task_id,tool,ok,revision,error_code) VALUES(?,?,?,?,?,?)',
                           (token_identity(x_agent_token),bound,tool,int(payload.get('ok') is True),payload.get('revision'),(payload.get('error') or {}).get('code')))
        return response

    @app.post("/tasks/{task_id}/review")
    async def user_review(task_id: str, request: Request,
                          x_agent_token: str | None = Header(default=None),
                          x_user: str | None = Header(default=None)):
        """人工复核只走用户会话；Agent 令牌一律无权调用。"""
        if x_agent_token or request.headers.get('authorization', '').lower().startswith('bearer '):
            return JSONResponse(status_code=403,
                                content=error_envelope("FORBIDDEN", "agent token cannot review"))
        if not x_user and not request.cookies.get("esg_user"):
            return JSONResponse(status_code=401,
                                content=error_envelope("UNAUTHORIZED", "user session required"))
        body = await _read_submission(request)
        if not body:
            return JSONResponse(status_code=400,
                                content=error_envelope("INVALID_INPUT", "invalid form or JSON"))
        try:
            raw_revision = body.get("expected_revision", -1)
            if isinstance(raw_revision, bool) or ("application/json" in request.headers.get("content-type", "") and type(raw_revision) is not int):
                raise ValueError('revision must be an integer')
            expected_revision = int(raw_revision)
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content=error_envelope(
                "INVALID_INPUT", "expected_revision required"))
        actor = x_user or request.cookies.get("esg_user") or "local_user"
        cmd = ReviewCommand(
            action=body.get("action", "set_check_status"),
            check_id=body.get("check_id"), new_status=body.get("new_status"),
            reason=body.get("reason", ""),
            expected_revision=expected_revision,
            target_key=body.get("target_key"),
        )
        rstore: ReviewStore = app.state.review_store
        # 复核与解析以同一数据库版本为准；短事务内不等待模型或网络。
        mappings = [rstore._tasks, rstore._checks, rstore._scores, rstore._events]
        snapshots = []
        try:
            with app.state.tasks.transaction() as db:
                row = db.execute('SELECT revision FROM tasks WHERE task_id=?', (task_id,)).fetchone()
                if row is None:
                    raise ReviewError("TASK_NOT_FOUND")
                revision = row[0]
                if expected_revision != revision:
                    raise ReviewError("REVISION_CONFLICT", "task changed; read latest results")
                snapshots = [(mapping, deepcopy(mapping.get(task_id))) for mapping in mappings]
                if task_id not in rstore._tasks:
                    rstore.create_task(task_id, revision=revision)
                else:
                    rstore._tasks[task_id]["revision"] = revision
                business = read_business(db, task_id)
                rstore._events[task_id] = business.get('review', {}).get('events', [])
                business.pop('score', None)
                for check in business['checks']:
                    rstore.put_check(task_id, check)
                task = do_review(rstore, task_id, cmd, actor=actor, is_agent=False)
                merged_checks = {c['check_id']: c for c in business['checks']}
                merged_checks.update(rstore._checks.get(task_id, {}))
                business['checks'] = list(merged_checks.values())
                for comparison in business['comparisons']:
                    comparison['is_current'] = False
                business['review'] = dict(task=task, events=rstore.audit_events(task_id))
                save_business(db, task_id, business)
                db.execute('UPDATE tasks SET revision=? WHERE task_id=?', (task["revision"], task_id))
        except BaseException as ex:
            # 既有复核仓储仍在内存；数据库提交失败时一并恢复其变更。
            for mapping, snapshot in snapshots:
                if snapshot is None:
                    mapping.pop(task_id, None)
                else:
                    mapping[task_id] = snapshot
            if not isinstance(ex, ReviewError):
                raise
            status = 409 if ex.code == "REVISION_CONFLICT" else 400
            payload = error_envelope(ex.code, str(ex))
            payload["task_id"] = task_id
            return JSONResponse(status_code=status, content=payload)
        score = rstore.get_score(task_id)
        if score is not None:
            score["is_current"] = False
        # 复核成功后旧导出标记过期（页面与下载仍可见，但不能当当前版本）
        artifacts: ArtifactStore = app.state.artifacts
        artifacts.invalidate_task(task_id)
        # 复核结果合并回全量检查仓储：只覆盖被复核的检查项，不得清空其他项
        confirmed = rstore._checks.get(task_id, {})
        merged = {c["check_id"]: c for c in app.state.checks.get(task_id, [])
                  if isinstance(c, dict) and c.get("check_id")}
        merged.update(confirmed)
        app.state.checks[task_id] = list(merged.values())
        # 复核改变了证据口径：本任务既有比较结果全部过期，须重算
        for c in app.state.comparisons_by_task.get(task_id, []):
            c["is_current"] = False
        return {"ok": True, "data": task, "error": None,
                "task_id": task_id, "revision": task["revision"]}

    # ---------------- Task 6：业务页面 / 导出 / PDF 查看 ----------------

    def require_user_session(x_user, x_agent_token, request=None):
        """用户路由：Agent 令牌一律无权；支持 header 或本地会话 cookie。"""
        if x_agent_token or (request is not None and request.headers.get('authorization', '').lower().startswith('bearer ')):
            return JSONResponse(status_code=403,
                                content=error_envelope("FORBIDDEN", "agent token cannot use user route"))
        cookie_user = request.cookies.get("esg_user") if request is not None else None
        if not x_user and not cookie_user:
            return JSONResponse(status_code=401,
                                content=error_envelope("UNAUTHORIZED", "user session required"))
        return None

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, task_id: str | None = None):
        store: InMemoryTaskStore = app.state.tasks
        if not task_id:
            task_id = next(iter(store._tasks), None)
        rec = store.get(task_id) if task_id else None
        if task_id and rec is None:
            return HTMLResponse("<h1>404 · 任务不存在</h1>", status_code=404)
        evidence_by_id = {e.evidence_id: e for e in task_evidence(task_id)} if task_id else {}
        checks = app.state.checks.get(task_id, []) if task_id else []
        comparisons = app.state.comparisons_by_task.get(task_id, [])
        if task_id and hasattr(store, 'transaction'):
            with store.transaction() as db:
                if db.execute('SELECT 1 FROM business_state WHERE task_id=?', (task_id,)).fetchone():
                    business = read_business(db, task_id)
                    checks, comparisons = business['checks'], business['comparisons']
        checks = [c for c in checks if c.get('is_current', True)]
        comparisons = [c for c in comparisons if c.get('is_current', True)]
        records = [r for r in app.state.records.list_by_task(task_id) if r.get('is_current', True)] if task_id else []
        try:
            score = do_score(checks, app.state.rulebook) if task_id else None
        except Exception:
            score = None
        artifact_store: ArtifactStore = app.state.artifacts
        artifacts = artifact_store.list_for_task(task_id) if task_id else []
        task_view = None
        if rec is not None:
            results = rec.results or {}
            task_view = {
                "task_id": task_id, "revision": rec.revision,
                "stage": results.get("stage", "metrics"),
                "report_ids": bound_reports(results),
            }
        response = templates.TemplateResponse(request, "index.html", {
            "task": task_view,
            "report_ids": (task_view or {}).get("report_ids", []),
            "evidence_by_id": evidence_by_id,
            "records": records,
            "checks": checks,
            "score": score,
            "comparisons": comparisons,
            "runs": list(reversed(app.state.runs.list_for_task(task_id))) if task_id else [],
            "artifacts": artifacts,
            "check_ids": list(ALL_CHECK_IDS),
        })
        # 本地单用户原型：访问页面即建立会话 cookie，浏览器表单无需手写 header
        response.set_cookie("esg_user", "local_user", httponly=True, samesite="lax")
        return response

    @app.get("/api/reports/{report_id}/pdf")
    def report_pdf(report_id: str, request: Request,
                   x_agent_token: str | None = Header(default=None),
                   x_user: str | None = Header(default=None)):
        """只服务已登记报告的 PDF 文件；不暴露任意路径。"""
        denied = require_user_session(x_user, x_agent_token, request)
        if denied:
            return denied
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", report_id):
            return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "report not found"))
        path = app.state.report_paths.get(report_id) or app.state.tasks.report_path(report_id)
        if path is None or not Path(path).exists():
            return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "report not found"))
        return FileResponse(str(path), media_type="application/pdf")

    @app.get("/api/artifacts/{artifact_id}")
    def artifact_download(artifact_id: str, request: Request,
                          x_agent_token: str | None = Header(default=None),
                          x_user: str | None = Header(default=None)):
        denied = require_user_session(x_user, x_agent_token, request)
        if denied:
            return denied
        artifact_store: ArtifactStore = app.state.artifacts
        meta = artifact_store.get_meta(artifact_id)
        if meta is None:
            return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "artifact not found"))
        actor = x_user or request.cookies.get('esg_user')
        if meta.get('user_identity') and meta['user_identity'] != 'user:' + actor:
            return JSONResponse(status_code=404, content=error_envelope('NOT_FOUND', 'artifact not found'))
        content = artifact_store.get_content(artifact_id)
        if content is None:
            return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "artifact not found"))
        headers = {"X-Artifact-Revision": str(meta["revision"]),
                   "X-Artifact-Current": "true" if meta["is_current"] else "false",
                   "Cache-Control": "no-store"}
        return Response(content, media_type=meta["media_type"], headers=headers)

    async def _read_submission(request: Request) -> dict:
        """同时接受 JSON 与网页表单（拒绝非字典）。"""
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                body = await request.json()
                return body if isinstance(body, dict) else {}
            except Exception:
                return {}
        form = await request.form()
        return dict(form)

    @app.post("/tasks/{task_id}/exports")
    async def create_export(task_id: str, request: Request,
                            x_agent_token: str | None = Header(default=None),
                            x_user: str | None = Header(default=None)):
        denied = require_user_session(x_user, x_agent_token, request)
        if denied:
            return denied
        body = await _read_submission(request)
        if 'application/json' not in request.headers.get('content-type', ''):
            try:
                body['expected_revision'] = int(body.get('expected_revision', -1))
            except (TypeError, ValueError):
                body = None
        actor = x_user or request.cookies.get('esg_user')
        try:
            response = await run_in_threadpool(app.state.export_service.run, task_id,
                'user:' + actor, request.headers.get('idempotency-key') or str(uuid4()), body)
            app.state.exports_by_task[task_id] = response['data']
            return response
        except ParseError as exc:
            return JSONResponse(status_code=exc.status, content=exc.payload)
        except Exception:
            return JSONResponse(status_code=500, content=error_envelope('INTERNAL_ERROR', 'Export failed'))

    @app.post("/tasks/{task_id}/agent-runs")
    async def start_agent_run(task_id: str, request: Request,
                              x_agent_token: str | None = Header(default=None),
                              x_user: str | None = Header(default=None)):
        denied = require_user_session(x_user, x_agent_token, request)
        if denied:
            return denied
        rec = app.state.tasks.get(task_id)
        if rec is None:
            return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "task not found"))
        body = await _read_submission(request)
        mode = str(body.get("mode", "full_evaluation"))
        if mode not in ("full_evaluation", "question", "resume"):
            return JSONResponse(status_code=422, content=error_envelope("INVALID_MODE", "bad mode"))
        try:
            expected_revision = int(body.get("expected_revision", -1))
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content=error_envelope(
                "INVALID_INPUT", "expected_revision required"))
        if expected_revision != rec.revision:
            return JSONResponse(status_code=409,
                                content={**error_envelope("REVISION_CONFLICT", "revision mismatch", True),
                                         "task_id": task_id, "revision": rec.revision})
        # 同任务串行：已有 queued/running 时拒绝重复启动
        active = [r for r in app.state.runs.list_for_task(task_id)
                  if r.status.value in ("queued", "running")]
        if active:
            return JSONResponse(status_code=409,
                                content={**error_envelope("RUN_BUSY", "task already has active run", True),
                                         "task_id": task_id, "revision": rec.revision})

        import threading
        import uuid

        # 用户问题作为固定业务模板数据，不把原文直接交给 SDK 命令路由
        import json
        question = body.get('question','')
        if not isinstance(question,str) or len(question)>4000:
            return JSONResponse(status_code=422, content=error_envelope('INVALID_INPUT','Question must be at most 4000 characters'))
        prompt = json.dumps({'task_id':task_id,'mode':mode,'revision':rec.revision,'user_question':question},ensure_ascii=False)
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        if mode == 'resume':
            previous = [r for r in app.state.runs.list_for_task(task_id) if r.status.value=='waiting_review']
            if not previous:
                return JSONResponse(status_code=422,content=error_envelope('INVALID_STATE','No review-paused session is available to resume'))
            session_id = previous[-1].session_id
        try:
            run = app.state.runs.create_queued(task_id, session_id=session_id)
        except RuntimeError:
            return JSONResponse(status_code=409,content={**error_envelope('RUN_BUSY','Task already has an active run'),'task_id':task_id,'revision':rec.revision})

        def worker():
            app.state.runs.mark_running(run.run_id)
            starter = app.state.agent_starter
            try:
                if starter is None:
                    # 未注入真实宿主时不得假装成功：明确 failed（离线页面演示）
                    raise RuntimeError("agent runner not configured for this task")
                starter(task_id, run.run_id, mode, prompt)
            except Exception as ex:
                app.state.runs.fail(run.run_id, f"{type(ex).__name__}: {ex}")

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True,
                "data": {"run_id": run.run_id, "status": run.status.value,
                         "task_id": task_id, "revision": rec.revision},
                "error": None, "task_id": task_id, "revision": rec.revision}

    @app.get("/tasks/{task_id}/agent-runs/{run_id}")
    def get_agent_run(task_id: str, run_id: str, request: Request,
                      x_agent_token: str | None = Header(default=None),
                      x_user: str | None = Header(default=None)):
        denied = require_user_session(x_user, x_agent_token, request)
        if denied:
            return denied
        try:
            run = app.state.runs.get(run_id)
        except KeyError:
            return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "run not found"))
        if run.task_id != task_id:
            return JSONResponse(status_code=404, content=error_envelope("NOT_FOUND", "run not found"))
        return {"ok": True,
                "data": {"run_id": run.run_id, "task_id": run.task_id,
                         "status": run.status.value, "answer": run.answer,
                         "error": run.error, "revision": run.revision},
                "error": None, "task_id": task_id}

    return app
