"""Real Node client -> real loopback FastAPI -> durable services; provider is an offline stub."""

import json
import socket
import subprocess
import threading
import time
from pathlib import Path
import pytest
import uvicorn
from esg_app.main import create_app
from esg_app.config import Settings
from esg_app.model_extractor import ModelCandidates
from test_metrics import candidate


def test_all_ten_tools_cross_node_http_boundary(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    if not (root / "harness-plugin/dist/client.js").exists():
        pytest.skip("build the TypeScript plugin before the cross-language bridge test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-bridge-key")
    app = create_app(
        Settings(database_path=str(tmp_path / "db"), extraction_model="deepseek-flash")
    )
    data = json.loads(
        (root / "contracts/examples/get_task_results.success.json").read_text()
    )["data"]
    data["task"].update(
        task_id="bridge", report_ids=["r1"], selected_years=[2023, 2024], revision=0
    )
    data["reports"][0]["report_id"] = "r1"
    app.state.tasks.register_task("bridge", 0, data)
    app.state.tasks.issue_agent_token("bridge", "offline-bridge-token")
    pdf = tmp_path / "offline.pdf"
    pdf.write_bytes(b"%PDF offline parser fixture")
    app.state.tasks.register_report_path("r1", pdf)
    app.state.parse_service.parser = lambda _: dict(
        page_count=1,
        pages=[
            dict(
                number=1,
                text="温室气体排放量 2023 100 2024 110 tCO2e",
                rows=[],
                issue=None,
                footnotes=[],
            )
        ],
    )

    def model(payload, key, timeout):
        data = json.loads(payload["messages"][1]["content"])
        assert data["untrusted_evidence"] and key == "offline-bridge-key"
        config = data["trusted_config"]
        candidates = [
            candidate(
                report_id="r1",
                year=year,
                value="100" if year == 2023 else "110",
                evidence_ids=config["allowed_evidence_ids"][:1],
                raw_unit="tCO2e",
            )
            for year in config["years"]
        ]
        return dict(
            choices=[
                dict(
                    finish_reason="stop",
                    message=dict(content=json.dumps(dict(candidates=candidates))),
                )
            ],
            usage=dict(prompt_tokens=55, completion_tokens=33),
        )

    app.state.extraction_deps.model_fn.transport = model
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    code = r"""
      import { BusinessClient } from './harness-plugin/dist/client.js';
      import assert from 'node:assert/strict';
      const c=new BusinessClient(process.argv[1],'offline-bridge-token','bridge');
      const seen=[];let revision=0;
      async function call(name,args={}){
        const r=await c.call(name,args,{callIdentity:'bridge/'+name});
        assert.equal(r.ok,true,JSON.stringify(r));seen.push(name);revision=r.revision;return r.data;
      }
      await call('get_task_results');await call('parse_report',{expected_revision:revision});
      await call('search_evidence',{query:'温室气体',metric_id:'E01',limit:5});
      await call('get_rulebook');
      const extraction=await call('extract_metrics',{expected_revision:revision,metric_ids:['E01'],years:[2023,2024]});
      assert.equal(extraction.run.usage.input_tokens,55);
      const ids=extraction.records.map(r=>r.record_id);
      await call('validate_metrics',{record_ids:ids});
      const cmp=await call('compare_years',{expected_revision:revision,older_record_id:ids[0],newer_record_id:ids[1]});
      assert.equal(cmp.status,'needs_review');assert.equal(cmp.percent_change,null);
      await call('save_check_proposals',{expected_revision:revision,checks:[{check_id:'C01',rule_version:'disclosure-v0.1',status:'unknown',evidence_ids:[],reason:'待核对',missing_information:[],suggestion:'',target_key:null}]});
      const score=await call('calculate_score');assert.equal(score.final_score,null);
      const exported=await call('export_analysis',{expected_revision:revision});assert.equal(exported.artifacts.length,3);
      const results=await c.getTaskResults();assert.equal(results.revision,revision);assert.equal(results.data.extraction_runs.length,1);assert.equal(results.data.task.stage,'exported');assert.equal(results.data.reports[0].status,'parsed');
      console.log(JSON.stringify({tools:seen,revision,artifacts:exported.artifacts.length}));
    """
    try:
        result = subprocess.run(
            ["node", "--input-type=module", "-e", code, f"http://127.0.0.1:{port}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert len(report["tools"]) == 10 and report["revision"] == 4
        with app.state.tasks.transaction() as db:
            assert (
                db.execute(
                    "SELECT COUNT(DISTINCT tool) FROM agent_tool_calls WHERE ok=1"
                ).fetchone()[0]
                == 10
            )
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
