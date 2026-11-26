"""Explicit offline browser-QA fixture; never loads SDK, keys or real task data.
Run from the checkout: python scripts/preview_business_fixture.py --port 8767
"""

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/src"))
from esg_app.main import create_app
from esg_app.config import Settings
from esg_app.retrieval import Evidence, EvidenceStore
from esg_app.parse_repository import save_business
from esg_app.run_jobs import RunStatus


def fixture_app(directory: Path):
    from reportlab.pdfgen import canvas

    app = create_app(
        Settings(
            database_path=str(directory / "fixture.sqlite3"), harness_enabled=False
        )
    )
    task_id = "offline_browser_fixture"
    report_id = "fixture_report"
    app.state.tasks.register_task(
        task_id,
        7,
        {
            "task": {
                "company_id": "offline-fixture",
                "is_fixture": True,
                "report_ids": [report_id],
                "selected_years": [2023, 2024],
            },
            "reports": [
                {
                    "report_id": report_id,
                    "company_name": "离线测试夹具（非真实企业）",
                    "report_year": 2024,
                    "sha256": "测试文件，不是实际报告哈希",
                }
            ],
        },
    )
    pdf = directory / "fixture.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(60, 760, "OFFLINE BROWSER FIXTURE - NOT A CORPORATE REPORT")
    c.save()
    app.state.tasks.register_report_path(report_id, pdf)
    app.state.evidence = EvidenceStore(
        [
            Evidence(
                "fixture_evidence",
                report_id,
                1,
                None,
                "table",
                "离线测试夹具：原值 0.1300；<img src=x onerror=alert(1)> 为普通文本。",
                viewer_url="https://ignored.invalid",
            )
        ]
    )
    checks = [
        {
            "check_id": "C01",
            "status": "unknown",
            "origin": "model",
            "review_state": "unreviewed",
            "reason": "离线测试夹具：缺少可判断依据",
            "evidence_ids": ["fixture_evidence"],
            "revision": 7,
            "is_current": True,
        }
    ]
    records = []
    for index, (raw, value, status) in enumerate(
        [
            ("0", "0", "extracted"),
            ("/", None, "missing_marker"),
            ("0.1300", "0.1300", "extracted"),
        ]
    ):
        records.append(
            {
                "record_id": f"fixture_{index}",
                "task_id": task_id,
                "report_id": report_id,
                "metric_id": f"E0{index+1}",
                "metric_year": 2024,
                "raw_value": raw,
                "value": value,
                "raw_unit": "MWh",
                "canonical_unit": "MWh",
                "conversion_rule": None,
                "denominator": None,
                "boundary_text": "离线测试夹具",
                "boundary_key": None,
                "method_text": "",
                "scope3_categories": [],
                "evidence_ids": ["fixture_evidence"],
                "status": status,
                "origin": "reported",
                "supersedes_id": None,
                "derivation": None,
                "revision": 7,
                "review_state": "unreviewed",
                "is_current": True,
                "extraction_note": "仅用于界面验收，单位与指标不代表正式映射",
            }
        )
    with app.state.tasks.transaction() as db:
        save_business(
            db,
            task_id,
            {
                "records": records,
                "checks": checks,
                "comparisons": [],
                "runs": [],
                "issues": [],
            },
        )
    app.state.checks[task_id] = checks

    def offline_starter(tid, run_id, mode, prompt):
        assert tid == task_id
        time.sleep(2)
        if mode == "question":
            app.state.runs.fail(
                run_id, "离线测试夹具：故意模拟运行失败 <script>not executable</script>"
            )
        elif mode == "resume":
            app.state.runs.complete(
                run_id,
                "离线测试夹具：恢复交互完成，不是SDK真实恢复。",
                app.state.tasks.get(tid).revision,
            )
        else:
            run = app.state.runs.get(run_id)
            run.status = RunStatus.WAITING_REVIEW
            run.answer = "离线测试夹具：请复核检查项；未调用模型。"
            run.revision = app.state.tasks.get(tid).revision
            app.state.runs.save(run)

    app.state.agent_starter = offline_starter
    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="esg-browser-fixture-") as directory:
        uvicorn.run(fixture_app(Path(directory)), host="127.0.0.1", port=args.port)
