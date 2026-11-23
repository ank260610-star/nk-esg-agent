"""Real pinned PDF/API regression. Gold is checked only after service output; no model calls."""

from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from esg_app.main import create_app
from esg_app.config import Settings
from esg_app.parse_service import PARSER_VERSION

EXPECTED_SHA = "8dfceabd75452ffaf39b0ffa7a1e841707b586931347819e058ebb343c93b1dd"
GOLD = {
    "E01": ("范围1+范围2温室气体排放量", 127, "吨二氧化碳当量", ["12341455.00", "10101826.20"]),
    "E02": (
        "温室气体排放密度（范围1+范围2）",
        127,
        "吨二氧化碳当量/万元营收",
        ["0.2049", "0.1300"],
    ),
    "E03": ("范围3温室气体排放量", 127, "吨二氧化碳当量", ["/", "3326312.49"]),
    "E04": ("能源总耗量", 125, "兆瓦时", ["16592592.44", "23552569.28"]),
    "E05": ("总取水量", 126, "立方米", ["73807222", "94268627"]),
    "E06": ("总耗水量", 126, "立方米", ["22067452", "55317707"]),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def norm(text):
    return re.sub(r"[\s,，]", "", text)


def label(text):
    return re.sub(r"\d{1,3}$", "", norm(text or ""))


def probe():
    args = json.load(sys.stdin)
    app = create_app(Settings(database_path=args["database"]))
    c = TestClient(app)
    response = c.post(
        "/api/tasks/byd_regression/parse",
        headers={"Authorization": f"Bearer {args['token']}", "Idempotency-Key": args["key"]},
        json={"expected_revision": 0},
    )
    evidence = app.state.tasks.evidence_for_task("byd_regression")
    print(
        json.dumps(
            {
                "response": response.json(),
                "revision": app.state.tasks.get("byd_regression").revision,
                "evidence_hash": sha(
                    json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()
                ),
            }
        )
    )
    c.close()
    app.state.tasks.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pdf", type=Path, required=True)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    data = args.pdf.read_bytes()
    assert sha(data) == EXPECTED_SHA, "PDF hash differs from reference original"
    now = datetime.now(timezone.utc)
    out = args.output or ROOT / "data/regressions" / now.strftime("byd-%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)
    app = create_app(Settings(database_path=str(out / "regression.sqlite3")))
    repo = app.state.tasks
    token = secrets.token_urlsafe(32)
    key = str(uuid4())
    task = "byd_regression"
    repo.register_task(task, 0, {"report_ids": ["byd_report"]})
    repo.issue_agent_token(task, token)
    repo.register_report_path("byd_report", args.pdf.resolve())
    c = TestClient(app)
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": key}
    checks = {}

    def check(name, ok):
        checks[name] = bool(ok)

    def parse(revision=0, k=key):
        return c.post(
            f"/api/tasks/{task}/parse",
            headers={**headers, "Idempotency-Key": k},
            json={"expected_revision": revision},
        )

    schema = json.loads((ROOT / "contracts/schema.json").read_text())

    def validate(name, obj):
        Draft202012Validator({**schema, "$ref": f"#/$defs/{name}"}).validate(obj)

    print("Parsing original PDF...", flush=True)
    start = time.monotonic()
    first = parse()
    duration = time.monotonic() - start
    result = {
        "source_base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_files_sha256": {
            str(p.relative_to(ROOT)): sha(p.read_bytes())
            for p in [
                ROOT / "backend/src/esg_app" / n
                for n in ["parsing.py", "parse_service.py", "retrieval.py"]
            ]
        },
        "started_at": now.isoformat(),
        "pdf_sha256": sha(data),
        "size_bytes": len(data),
        "source_url": "https://static.cninfo.com.cn/finalpage/2025-03-25/1222881489.PDF",
        "parser_version": PARSER_VERSION,
        "python": sys.version.split()[0],
        "dependencies": {
            n: importlib.metadata.version(n)
            for n in ["pdfplumber", "pdfminer.six", "fastapi", "httpx"]
        },
        "transport": "ASGI API + real PDF/SQLite; fresh Python process recovery; no model or SDK",
        "parse_seconds": round(duration, 3),
        "parse_response": first.json(),
        "checks": checks,
        "metrics": {},
    }
    validate("parse_report_output", first.json())
    check("parse_success", first.status_code == 200 and first.json()["ok"])
    if not checks["parse_success"]:
        (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    evidence = repo.evidence_for_task(task)
    for e in evidence:
        validate("Evidence", e)
    check("all_evidence_schema_valid", True)
    check("155_pages", first.json()["data"]["reports"][0]["page_count"] == 155)
    check("all_pages_readable", first.json()["data"]["reports"][0]["parsed_pages"] == 155)
    check("first_revision_once", first.json()["revision"] == 1)
    check("same_key_original_response", parse().json() == first.json())
    conflict = parse(1)
    check(
        "different_request_409",
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT",
    )
    cache = parse(1, str(uuid4()))
    check("cache_no_new_revision", cache.status_code == 200 and cache.json()["revision"] == 1)
    check("cache_no_duplicate_evidence", repo.evidence_for_task(task) == evidence)
    stale = parse(0, str(uuid4()))
    check(
        "old_revision_409",
        stale.status_code == 409 and stale.json()["error"]["code"] == "REVISION_CONFLICT",
    )
    bad = c.post("/api/tasks/foreign/parse", headers=headers, json={"expected_revision": 0})
    check("foreign_task_denied", bad.status_code == 403 and bad.json()["task_id"] is None)
    raw = {
        n: "\n".join(e["text"] for e in evidence if e["pdf_page"] == n and e["kind"] == "text")
        for n in [42, 125, 126, 127]
    }
    for mid, (name, page, unit, values) in GOLD.items():
        query = "温室气体排放密度" if mid == "E02" else name
        res = c.post(
            "/agent/tool",
            headers={"X-Agent-Token": token},
            json={
                "tool": "search_evidence",
                "arguments": {"query": query, "metric_id": mid, "limit": 5},
            },
        )
        returned = res.json()["data"]["evidence"]
        validate("search_evidence_output", res.json())
        years = {}
        candidates = [
            e
            for e in evidence
            if e["kind"] == "table" and e["pdf_page"] == page and label(e["row_label"]) == name
        ]
        for year, value in zip([2023, 2024], values):
            matched = []
            for e in candidates:
                if e["column_year"] != year:
                    continue
                parts = e["text"].rsplit(f" {year} ", 1)
                if len(parts) == 2 and norm(parts[1]) == norm(value + " " + unit):
                    matched.append(e["evidence_id"])
            years[str(year)] = {"expected": value, "match": bool(matched), "evidence_ids": matched}
        contaminated = any(re.search(r"\d{1,3}(?:,\d{3})+", e["row_label"]) for e in candidates)
        result["metrics"][mid] = {
            "label": name,
            "expected_page": page,
            "unit": unit,
            "years": years,
            "raw_values_present": all(norm(v) in norm(raw[page]) for v in values if v != "/"),
            "top5_page_hit": any(e["pdf_page"] == page for e in returned[:5]),
            "label_contaminated": contaminated,
        }
        if mid == "E03":
            notes = [
                e for e in returned if e["kind"] == "footnote" and re.match(r"^21\s", e["text"])
            ]
            check("scope3_footnote21_returned", bool(notes))
            check(
                "scope3_bidirectional_links",
                bool(notes)
                and any(
                    note["evidence_id"] in body["linked_evidence_ids"]
                    and body["evidence_id"] in note["linked_evidence_ids"]
                    for note in notes
                    for body in candidates
                ),
            )
    check("e02_cross_reference_kept", all("0.1300" in raw[n] for n in [42, 127]))
    c.get("/")
    pdf = c.get("/api/reports/byd_report/pdf")
    check("pdf_download_hash", pdf.status_code == 200 and sha(pdf.content) == EXPECTED_SHA)
    digest = sha(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode())
    c.close()
    repo.close()
    process = subprocess.run(
        [sys.executable, str(Path(__file__)), "--probe"],
        input=json.dumps({"database": str(out / "regression.sqlite3"), "token": token, "key": key}),
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )
    restored = json.loads(process.stdout) if process.returncode == 0 else {}
    check("process_restart_original_response", restored.get("response") == first.json())
    check("process_restart_revision", restored.get("revision") == 1)
    check("process_restart_evidence", restored.get("evidence_hash") == digest)
    result["evidence_count"] = len(evidence)
    result["evidence_kinds"] = dict(Counter(e["kind"] for e in evidence))
    result["summary"] = {
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "raw_metrics": sum(m["raw_values_present"] for m in result["metrics"].values()),
        "top5_page_hits": sum(m["top5_page_hit"] for m in result["metrics"].values()),
        "year_matches": sum(
            v["match"] for m in result["metrics"].values() for v in m["years"].values()
        ),
        "year_total": 12,
    }
    result["overall_pass"] = (
        all(checks.values())
        and result["summary"]["year_matches"] == 12
        and result["summary"]["top5_page_hits"] == 6
        and not any(m["label_contaminated"] for m in result["metrics"].values())
    )
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {"output": str(out), "overall_pass": result["overall_pass"], **result["summary"]},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if result["overall_pass"] else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        probe()
    else:
        raise SystemExit(main())
