"""Run the real PDF -> Harness -> domain-tool chain, or perform a credential-safe preflight.

Use --check first. Missing prerequisites exit 2 and are never reported as a passing run.
No credential values are printed; .env is read only as literal KEY=VALUE data.
"""

from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from esg_app.config import Settings


def load_env(path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if not separator:
            raise ValueError("Environment file must contain literal KEY=VALUE lines")
        if (
            name != "DEEPSEEK_API_KEY"
            and name != "DSH_MODEL"
            and not name.startswith("ESG_")
        ):
            raise ValueError("Environment file contains an unsupported variable")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", type=Path, default=ROOT / ".env")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true")
    action.add_argument("--serve", action="store_true")
    p.add_argument("--database", type=Path)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--pdf", type=Path)
    p.add_argument("--company-id")
    p.add_argument("--company-name")
    p.add_argument("--report-year", type=int)
    p.add_argument("--years", type=int, nargs="+")
    p.add_argument(
        "--mode", choices=["full_evaluation", "question"], default="full_evaluation"
    )
    p.add_argument("--question", default="")
    p.add_argument("--output", type=Path, default=ROOT / "data/task7-live")
    args = p.parse_args()
    load_env(args.env_file)
    settings = Settings.from_env()
    if args.serve:
        if (
            not args.database
            or not args.database.is_file()
            or not 1 <= args.port <= 65535
        ):
            p.error("--serve requires an existing --database and a port in 1..65535")
        import uvicorn
        from esg_app.main import create_app

        settings = replace(
            settings,
            database_path=str(args.database.resolve()),
            agent_api_base_url=f"http://127.0.0.1:{args.port}",
        )
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)
        return 0
    missing = []
    for name in [
        "DEEPSEEK_API_KEY",
        "ESG_EXTRACTION_MODEL",
        "DSH_MODEL",
        "ESG_DSH_HOME",
        "ESG_WORKSPACE",
        "ESG_HARNESS_PATCH",
    ]:
        if not os.environ.get(name, "").strip():
            missing.append(name)
    if not importlib.util.find_spec("deepseek_harness"):
        missing.append("deepseek-harness-sdk==0.2.2rc2")
    if not (ROOT / "harness-plugin/dist/index.js").is_file():
        missing.append("built TypeScript plugin")
    if settings.harness_patch and not Path(settings.harness_patch).is_file():
        missing.append("existing prepared plugin patch")
    if missing:
        print(
            json.dumps(
                dict(status="blocked", model_called=False, missing=missing),
                ensure_ascii=False,
            )
        )
        return 2
    from esg_app.harness_runner import build_sdk_kwargs

    settings = replace(settings, harness_enabled=True)
    build_sdk_kwargs(settings)
    if args.check:
        print(json.dumps(dict(status="preflight_ready", model_called=False)))
        return 0
    if (
        not args.pdf
        or not args.company_id
        or not args.company_name
        or not args.report_year
        or not args.years
    ):
        p.error(
            "Real runs require --pdf, --company-id, --company-name, --report-year and --years"
        )
    import pdfplumber
    import uvicorn
    from esg_app.main import create_app
    from esg_app.run_jobs import RunStatus

    pdf = args.pdf.resolve()
    raw = pdf.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    with pdfplumber.open(pdf) as document:
        page_count = len(document.pages)
    args.output.mkdir(parents=True, exist_ok=False)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    settings = replace(
        settings,
        database_path=str(args.output / "task.sqlite3"),
        agent_api_base_url=f"http://127.0.0.1:{port}",
    )
    app = create_app(settings)
    task_id = "task_" + uuid4().hex
    report_id = "rpt_" + digest[:20]
    now = datetime.now(timezone.utc).isoformat()
    task = dict(
        task_id=task_id,
        company_id=args.company_id,
        report_ids=[report_id],
        selected_years=args.years,
        rule_version="disclosure-v0.1",
        stage="registered",
        revision=0,
        created_at=now,
        updated_at=now,
        error=None,
    )
    report = dict(
        report_id=report_id,
        sha256=digest,
        company_id=args.company_id,
        company_name=args.company_name,
        report_year=args.report_year,
        published_date=None,
        source_url=None,
        kind="sustainability",
        media_type="application/pdf",
        page_count=page_count,
        parser_version=None,
        status="registered",
        issues=[],
    )
    app.state.tasks.register_task(task_id, 0, dict(task=task, reports=[report]))
    app.state.tasks.register_report_path(report_id, pdf)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("Loopback business server did not start")
    run = app.state.runs.start(task_id, "session_" + uuid4().hex)
    try:
        try:
            app.state.agent_starter(
                task_id,
                run.run_id,
                args.mode,
                json.dumps({"user_question": args.question}, ensure_ascii=False),
            )
        except Exception:
            app.state.runs.fail(
                run.run_id,
                "Real Harness run failed; inspect task state before retrying",
            )
        run = app.state.runs.get(run.run_id)
        with app.state.tasks.transaction() as db:
            calls = [
                dict(tool=t, ok=bool(ok), revision=r, error=error)
                for t, ok, r, error in db.execute(
                    "SELECT tool,ok,revision,error_code FROM agent_tool_calls WHERE task_id=? ORDER BY id",
                    (task_id,),
                )
            ]
        summary = dict(
            status=run.status.value,
            task_id=task_id,
            session_id=run.session_id,
            revision=run.revision,
            pdf_sha256=digest,
            backend_tool_calls=calls,
            real_model_run_attempted=True,
            error=run.error,
        )
        (args.output / "run-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        )
        if run.answer:
            (args.output / "answer.md").write_text(run.answer, encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if run.status in (RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_LIMITATIONS, RunStatus.WAITING_REVIEW) else 1
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        app.state.tasks.close()


if __name__ == "__main__":
    raise SystemExit(main())
