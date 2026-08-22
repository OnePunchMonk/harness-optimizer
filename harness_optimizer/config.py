from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml


@dataclasses.dataclass
class Config:
    # Path to the harness codebase being evolved (a git repo or plain dir).
    harness_dir: Path

    # Shell command run against a candidate harness dir to score it.
    # Must print a single JSON object to stdout: {"score": <float>, ...}
    # The literal token {harness_dir} is substituted with the candidate's path.
    eval_cmd: str

    # Directory where variant working copies, the archive, and logs are kept.
    work_dir: Path = Path(".harness_optimizer")

    # Population / search
    generations: int = 10
    children_per_generation: int = 4
    archive_size_cap: int = 200  # 0 = unbounded

    # Parent selection: "score_weighted" (DGM-style, favors high score + few
    # attempts) or "uniform" (pure random, more exploratory) or "best" (hill-climb).
    parent_selection: str = "score_weighted"

    # Command used to invoke the mutating agent. Must accept a prompt on stdin
    # (or via {prompt_file}) and edit files in {cwd} in place.
    # Default targets Claude Code headless mode.
    mutator_cmd: str = "claude -p --dangerously-skip-permissions"

    # Optional free-text description of what "better" means for this harness,
    # folded into the mutation prompt (e.g. "reduce tool-call count while
    # keeping task success rate", "improve SWE-bench resolve rate").
    objective: str = "Improve the evaluation score without breaking existing behavior."

    # Files/globs the mutator is allowed to touch. Empty = no restriction.
    allowed_paths: list[str] = dataclasses.field(default_factory=list)

    eval_timeout_s: int = 1800
    mutate_timeout_s: int = 1800

    @staticmethod
    def load(path: str | Path) -> "Config":
        raw = yaml.safe_load(Path(path).read_text())
        raw["harness_dir"] = Path(raw["harness_dir"]).expanduser().resolve()
        raw["work_dir"] = Path(raw.get("work_dir", ".harness_optimizer")).expanduser().resolve()
        return Config(**raw)
