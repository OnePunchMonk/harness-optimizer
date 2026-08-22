from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from .config import Config
from .node import Node

MUTATION_STRATEGIES = [
    "Propose one focused change to a prompt, tool description, or control-flow "
    "rule that you believe will improve the score. Prefer precise, testable edits "
    "over broad rewrites.",
    "Look for a failure mode implied by low scores or error notes on the parent "
    "and fix specifically that.",
    "Simplify: remove or consolidate an instruction/tool that seems redundant or "
    "counterproductive, without losing capability.",
    "Try a structural change: reorder instructions, change how tools are "
    "described or gated, or adjust examples/few-shot content.",
    "Make a small, orthogonal exploratory change unrelated to the parent's "
    "known weaknesses, to diversify the search.",
]


def build_prompt(cfg: Config, parent: Node, strategy: str) -> str:
    history = ""
    if parent.mutation_notes:
        history = f"\nThe parent variant's own last change was:\n{parent.mutation_notes}\n"
    score_line = f"\nThe parent variant scored: {parent.score} (metrics: {parent.metrics})\n" if parent.score is not None else ""
    scope = ""
    if cfg.allowed_paths:
        scope = f"\nYou may only modify files matching: {cfg.allowed_paths}\n"

    return f"""You are evolving an AI agent harness (its prompts, tool
definitions, and/or orchestration logic) to improve a measurable objective.

Objective: {cfg.objective}
{score_line}{history}{scope}
Mutation strategy for this attempt: {strategy}

Instructions:
1. Read the harness code in the current directory to understand it.
2. Make ONE coherent, self-contained edit implementing the strategy above.
   Keep the change small enough to be independently evaluable.
3. Do not break the harness's ability to run (preserve its interfaces/APIs
   unless the objective explicitly calls for changing them).
4. When done, write a one-paragraph summary of exactly what you changed and
   why to a file named MUTATION_NOTES.md in the current directory (overwrite
   if it exists). This is the only required output file.
"""


def make_variant_dir(cfg: Config, parent: Node, variant_id: str) -> Path:
    dest = cfg.work_dir / "variants" / variant_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(parent.dir, dest, ignore=shutil.ignore_patterns(".git"))
    return dest


def run_mutation(cfg: Config, variant_dir: Path, prompt: str) -> tuple[bool, str, str]:
    """Returns (success, mutation_notes, error)."""
    cmd = shlex.split(cfg.mutator_cmd)
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            cwd=str(variant_dir),
            capture_output=True,
            text=True,
            timeout=cfg.mutate_timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, "", "mutator timed out"

    if proc.returncode != 0:
        return False, "", f"mutator exited {proc.returncode}: {proc.stderr[-2000:]}"

    notes_file = variant_dir / "MUTATION_NOTES.md"
    notes = notes_file.read_text() if notes_file.exists() else proc.stdout[-2000:]
    return True, notes, ""


def diff_against_parent(parent_dir: Path, variant_dir: Path) -> str:
    proc = subprocess.run(
        ["diff", "-ruN", "--exclude=.git", str(parent_dir), str(variant_dir)],
        capture_output=True,
        text=True,
    )
    return proc.stdout[-20000:]
