from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from .config import Config


def run_eval(cfg: Config, variant_dir: Path) -> tuple[bool, float | None, dict, str]:
    """Returns (success, score, metrics, error)."""
    cmd = cfg.eval_cmd.replace("{harness_dir}", str(variant_dir))
    try:
        proc = subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=cfg.eval_timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, None, {}, "eval timed out"

    if proc.returncode != 0:
        return False, None, {}, f"eval exited {proc.returncode}: {proc.stderr[-2000:]}"

    # Look for the last JSON object printed to stdout.
    out = proc.stdout.strip().splitlines()
    for line in reversed(out):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "score" not in result:
            return False, None, {}, f"eval JSON missing 'score' key: {result}"
        score = float(result["score"])
        metrics = {k: v for k, v in result.items() if k != "score"}
        return True, score, metrics, ""

    return False, None, {}, f"no JSON object with 'score' found in eval stdout: {proc.stdout[-1000:]}"
