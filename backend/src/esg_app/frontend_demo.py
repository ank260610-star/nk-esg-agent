"""Local page host; demo interactions run only in the browser."""
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[2]
SAMPLE_PATHS = (
    ROOT / "data/reports/比亚迪_2024年度可持续发展报告.pdf",
    ROOT / "队友筛选/发给候选人/比亚迪_2024年度可持续发展报告.pdf",
)


def create_app(sample_pdf: Path | None = None) -> FastAPI:
    app = FastAPI(title="ESG Workbench · Frontend Demo", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE / "demo_templates")

    def find_sample() -> Path | None:
        candidates = (sample_pdf,) if sample_pdf is not None else SAMPLE_PATHS
        return next((p for p in candidates if p.is_file()), None)

    @app.middleware("http")
    async def page_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; frame-src 'self' blob:; object-src 'none'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "frontend-demo", "model_connected": False}

    @app.get("/demo/report-status")
    def report_status():
        return {"available": find_sample() is not None}

    @app.get("/demo/report")
    def report():
        path = find_sample()
        if path is None:
            raise HTTPException(404, "本机未安装公开样例PDF，请在页面查看人工参考摘要或选择本地PDF预览。")
        return FileResponse(path, media_type="application/pdf", filename="BYD-2024-sustainability.pdf", content_disposition_type="inline")

    return app


app = create_app()
