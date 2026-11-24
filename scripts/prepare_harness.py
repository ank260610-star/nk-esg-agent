"""Prepare an explicit isolated SDK profile without making any model calls.

Requires the pinned SDK, a built harness-plugin/dist, and pnpm on PATH.
"""

from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home", type=Path, required=True)
    p.add_argument("--workspace", type=Path, required=True)
    args = p.parse_args()
    home, workspace = args.home.resolve(), args.workspace.resolve()
    if home == workspace or home in workspace.parents or workspace in home.parents:
        p.error("home and workspace must be separate directories")
    if not shutil.which("pnpm"):
        p.error(
            "pnpm is required; run through npm exec --package=pnpm@10.29.2 -- <python> scripts/prepare_harness.py ..."
        )
    if not (ROOT / "harness-plugin/dist/client.js").is_file():
        p.error("Build the plugin with npm run build first")
    from deepseek_harness_runtime import resolve_bundled_launch_args

    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DSH_HOME": str(home)}
    launch = list(resolve_bundled_launch_args())
    subprocess.run(
        launch + ["--profile", "sdk", "--dump-default-config"],
        env=env,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    subprocess.run(
        launch
        + ["plugin", "--profile", "sdk", "add", "file:" + str(ROOT / "harness-plugin")],
        env=env,
        check=True,
    )
    installed = home / "profiles/sdk/node_modules/nk-esg-harness-plugin/dist"
    for filename in ("index.js", "client.js", "tools.js"):
        if (installed / filename).read_bytes() != (
            ROOT / "harness-plugin/dist" / filename
        ).read_bytes():
            raise RuntimeError(
                "Installed profile plugin differs from the current build"
            )
    # A separate invocation patch leaves existing profile customization untouched.
    patch = home / "esg-tools.patch.yml"
    patch.write_text(
        "- id: system-prompt\n  config:\n    personaPrefix: 你是环境信息披露质量分析助手，只使用获准的 ESG 领域工具；证据不足时保留未知。\n- insert:\n    - id: nk-esg-tools\n      name: nk-esg-harness-plugin\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "prepared_without_model_call",
                "home": str(home),
                "workspace": str(workspace),
                "patch": str(patch),
            }
        )
    )


if __name__ == "__main__":
    main()
