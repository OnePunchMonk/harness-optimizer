#!/usr/bin/env python3
"""
Real eval_cmd for the codex-rs target: for each task under
examples/codex_tasks/*, run the codex binary (built from --harness-dir)
against the task repo via the Claude shim, then grade by running the task's
test command. Score = fraction of tasks whose tests pass afterward.

Usage:
  codex_eval.py --harness-dir /path/to/codex/codex-rs/checkout

Requires:
  - a codex-cli release binary already built at
    <harness_dir>/target/release/codex (build once outside the loop;
    the optimizer only mutates prompt/source files, it doesn't rebuild --
    see README for why, and re-run `cargo build --release -p codex-cli`
    yourself if you mutate files that need recompilation).
  - shim/claude_responses_shim.py reachable two directories up from this
    script (i.e. this script staying inside examples/).
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = Path(__file__).resolve().parent / "codex_tasks"
SHIM = ROOT / "shim" / "claude_responses_shim.py"

TASK_TIMEOUT_S = 300


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_shim(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(SHIM), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("shim did not start in time")


def cargo_workspace_dir(harness_dir: Path) -> Path:
    if (harness_dir / "Cargo.toml").exists():
        return harness_dir
    if (harness_dir / "codex-rs" / "Cargo.toml").exists():
        return harness_dir / "codex-rs"
    raise RuntimeError(f"no Cargo workspace found under {harness_dir}")


def build_codex_binary(harness_dir: Path) -> Path:
    """
    Mutations can touch prompt .md files, which codex-rs embeds at compile
    time via include_str! (see protocol/src/models.rs), so a source-only
    change requires a real rebuild before it's observable. This is the
    dominant cost of evaluating a codex-rs variant.
    """
    ws = cargo_workspace_dir(harness_dir)
    proc = subprocess.run(
        ["cargo", "build", "--release", "-p", "codex-cli"],
        cwd=str(ws), capture_output=True, text=True, timeout=1800,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cargo build failed:\n{proc.stderr[-4000:]}")
    binary = ws / "target" / "release" / "codex"
    if not binary.exists():
        raise RuntimeError(f"cargo build succeeded but binary missing at {binary}")
    return binary


def run_task(codex_bin: Path, task_dir: Path, port: int, codex_home: Path) -> bool:
    task = json.loads((task_dir / "task.json").read_text())
    work = Path(tempfile.mkdtemp(prefix="codex_task_"))
    shutil.copytree(task_dir / "repo", work, dirs_exist_ok=True)

    codex_home.mkdir(parents=True, exist_ok=True)
    # Root-level keys must precede any [table] header in TOML, or they get
    # parsed as belonging to that table instead of the document root.
    (codex_home / "config.toml").write_text(f"""
model_provider = "claude-shim"
model = "claude-shim"
approval_policy = "never"
sandbox_mode = "danger-full-access"

[model_providers.claude-shim]
name = "claude-shim"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
requires_openai_auth = false
""")

    try:
        subprocess.run(
            [str(codex_bin), "exec", "--skip-git-repo-check", task["prompt"]],
            cwd=str(work),
            env={"CODEX_HOME": str(codex_home), "PATH": "/usr/bin:/bin:/usr/local/bin"},
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=TASK_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        pass  # graded by tests regardless

    test_result = subprocess.run(
        task["test_cmd"], shell=True, cwd=str(work),
        capture_output=True, text=True, timeout=120,
    )
    shutil.rmtree(work, ignore_errors=True)
    return test_result.returncode == 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness-dir", required=True)
    args = ap.parse_args()

    harness_dir = Path(args.harness_dir).resolve()

    try:
        codex_bin = build_codex_binary(harness_dir)
    except Exception as e:
        print(f"build failed: {e}", file=sys.stderr)
        sys.exit(1)

    tasks = sorted(p for p in TASKS_DIR.iterdir() if (p / "task.json").exists())

    port = free_port()
    shim_proc = start_shim(port)
    codex_home = Path(tempfile.mkdtemp(prefix="codex_home_"))

    try:
        passed = 0
        for task_dir in tasks:
            ok = run_task(codex_bin, task_dir, port, codex_home)
            passed += ok
    finally:
        shim_proc.terminate()
        shutil.rmtree(codex_home, ignore_errors=True)

    score = passed / len(tasks) if tasks else 0.0
    print(json.dumps({"score": score, "tasks_passed": passed, "tasks_total": len(tasks)}))


if __name__ == "__main__":
    main()
