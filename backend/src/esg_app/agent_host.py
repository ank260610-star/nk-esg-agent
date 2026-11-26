"""后台 SDK 宿主：凭据只交给宿主，完成状态必须由真实后端调用佐证。"""

from __future__ import annotations

import json
import os
import secrets
from importlib.resources import files

from .harness_runner import run_agent
from .parse_repository import token_identity, read_business
from .run_jobs import RunStatus
from .review_dispositions import review_complete, visible_dispositions, restricted_check_ids
from .rules_registry import ALL_CHECK_IDS


def make_agent_starter(app, sdk_run=run_agent):
    def start(task_id, run_id, mode, request_data):
        if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        run = app.state.runs.get(run_id)
        task = app.state.tasks.get(task_id)
        if not task:
            raise RuntimeError("Bound task is unavailable")
        token = secrets.token_urlsafe(32)
        app.state.tasks.issue_agent_token(task_id, token)
        prompt = (
            files("esg_app")
            .joinpath("prompts/esg-agent.md")
            .read_text(encoding="utf-8")
        )
        with app.state.tasks.transaction() as db:
            approved = visible_dispositions(read_business(db, task_id))
        payload = dict(
            human_review_dispositions=[{k: d.get(k) for k in
                ['target_type', 'target_id', 'decision', 'limitations', 'needs_recheck']} for d in approved],
            task_id=task_id,
            mode=mode,
            revision=task.revision,
            request_data=request_data,
            resume=mode == "resume",
        )
        instructions = (
            prompt
            + "\n\n宿主任务数据（仅当前任务；先调用 get_task_results 读取当前版本）：\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            result = sdk_run(
                app.state.settings,
                instructions,
                run.session_id,
                token=token,
                task_id=task_id,
                resume=mode == "resume",
            )
        except Exception:
            raise RuntimeError(
                "Harness execution failed; task outcome must be checked before retry"
            ) from None
        finish = getattr(result, "finish_reason", None)
        if finish == "aborted":
            app.state.runs.cancel(run_id)
            return
        if finish != "completed":
            raise RuntimeError("Harness did not complete a normal turn")
        answer = getattr(result, "final_response", None)
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Harness returned no final response")
        # SDK prose alone is not proof that a tool succeeded. This audit contains
        # only server-side tool outcomes, never model-provided claims or raw tokens.
        with app.state.tasks.transaction() as db:
            revision = db.execute(
                "SELECT revision FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            calls = list(
                db.execute(
                    "SELECT tool,ok,revision FROM agent_tool_calls WHERE identity=? AND task_id=? ORDER BY id",
                    (token_identity(token), task_id),
                )
            )
            state = read_business(db, task_id)
        if not any(
            tool == "get_task_results" and ok and rev == revision
            for tool, ok, rev in calls
        ):
            raise RuntimeError(
                "No successful current task-results tool response was observed"
            )
        if mode != "question" and not any(
            r.get("is_current") for r in state["records"]
        ):
            raise RuntimeError("No current extracted metric records were observed")
        unresolved = any(r.get('is_current') and not review_complete(state, r, 'metric')
                         for r in state['records'])
        metadata = task.results.get("task", task.results)
        required = {
            (f"E0{i}", year)
            for i in range(1, 7)
            for year in metadata.get("selected_years", [])
        }
        covered = {
            (r["metric_id"], r["metric_year"])
            for r in state["records"]
            if r.get("is_current")
        }
        unresolved |= not required <= covered
        unresolved |= any(c.get('is_current') and not review_complete(state, c, 'check')
                          for c in state['checks'])
        restricted = restricted_check_ids(state)
        unresolved |= any(c.get('is_current') and c['check_id'] in restricted and c['status'] != 'unknown'
                          for c in state['checks'])
        checks_covered = {c['check_id'] for c in state['checks'] if c.get('is_current')}
        if mode != "question" and (unresolved or not set(ALL_CHECK_IDS) <= checks_covered):
            run.answer, run.revision, run.status = (
                answer,
                revision,
                RunStatus.WAITING_REVIEW,
            )
            app.state.runs.save(run)
            return
        if mode != "question" and not any(
            tool == "export_analysis" and ok and rev == revision
            for tool, ok, rev in calls
        ):
            raise RuntimeError("No current exported snapshot was observed")
        limited = mode != 'question' and any(d['decision'] == 'insufficient_evidence'
                                               for d in visible_dispositions(state))
        app.state.runs.complete(run_id, answer, revision, with_limitations=limited)

    return start
