"""Initialize the real pinned SDK/plugin without invoking a model.

Pass --resume-session-id only for a persisted session from a real prior turn.
An empty cwd-only session is not proof of durable conversation recovery.
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home", type=Path, required=True)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--patch", type=Path, required=True)
    p.add_argument("--resume-session-id")
    args = p.parse_args()
    from importlib.metadata import version
    from deepseek_harness import DeepSeekHarness

    with DeepSeekHarness(
        dsh_home=str(args.home.resolve()),
        cwd=str(args.workspace.resolve()),
        profile="sdk",
        model="deepseek-flash",
        patches=(str(args.patch.resolve()),),
        initialize_timeout_seconds=60,
        request_timeout_seconds=30,
        env={
            "DSH_PERMISSION_MODE": "read-only",
            "ESG_API_BASE_URL": "http://127.0.0.1:8000",
            "ESG_TASK_ID": "runtime-probe",
            "ESG_AGENT_TOKEN": "offline-unissued",
        },
    ) as h:
        session = (
            h.resume_session(args.resume_session_id)
            if args.resume_session_id
            else h.start_session("boot_" + uuid4().hex)
        )
        cwd = session.get_working_directory()
        print(
            json.dumps(
                dict(
                    status="sdk_session_initialized",
                    sdk=version("deepseek-harness-sdk"),
                    runtime=version("deepseek-harness-runtime-bin"),
                    model_called=False,
                    session_cwd_verified=Path(cwd) == args.workspace.resolve(),
                    resume_checked=bool(args.resume_session_id),
                )
            )
        )


if __name__ == "__main__":
    main()
