from __future__ import annotations

import random
import shutil
import string
import time
from pathlib import Path

from .archive import Archive
from .config import Config
from .evaluate import run_eval
from .mutate import (
    MUTATION_STRATEGIES,
    build_prompt,
    diff_against_parent,
    enforce_allowed_paths,
    make_variant_dir,
    run_mutation,
)
from .node import Node


def _rand_id(n=8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Optimizer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        self.archive = Archive(self.cfg.work_dir / "archive.json")

    def seed_root(self) -> Node:
        existing_roots = [n for n in self.archive.nodes.values() if n.parent_id is None]
        if existing_roots:
            root = existing_roots[0]
            if root.status == "scored":
                _log(f"root already scored: {root.id} score={root.score}")
                return root
        else:
            root_dir = self.cfg.work_dir / "variants" / "root"
            if root_dir.exists():
                shutil.rmtree(root_dir)
            shutil.copytree(self.cfg.harness_dir, root_dir, ignore=shutil.ignore_patterns(".git"))
            root = Node(
                id="root",
                parent_id=None,
                generation=0,
                dir=str(root_dir),
                diff="",
                mutation_prompt="",
                mutation_notes="original, unmodified harness",
            )
            self.archive.add(root)

        _log("scoring root...")
        ok, score, metrics, err = run_eval(self.cfg, Path(root.dir))
        root.status = "scored" if ok else "eval_failed"
        root.score, root.metrics, root.error = score, metrics, err
        self.archive.add(root)
        if not ok:
            raise RuntimeError(f"root harness failed to evaluate: {err}")
        _log(f"root score: {score}")
        return root

    def step(self, generation: int) -> None:
        for i in range(self.cfg.children_per_generation):
            parent = self.archive.sample_parent(self.cfg.parent_selection)
            parent.n_children += 1
            self.archive.save()

            strategy = MUTATION_STRATEGIES[(generation * self.cfg.children_per_generation + i) % len(MUTATION_STRATEGIES)]
            variant_id = f"g{generation}_{_rand_id()}"
            variant_dir = make_variant_dir(self.cfg, parent, variant_id)
            prompt = build_prompt(self.cfg, parent, strategy)

            _log(f"[{variant_id}] mutating from parent={parent.id} strategy={strategy[:50]}...")
            mok, notes, merr = run_mutation(self.cfg, variant_dir, prompt)

            node = Node(
                id=variant_id,
                parent_id=parent.id,
                generation=generation,
                dir=str(variant_dir),
                diff="",
                mutation_prompt=prompt,
                mutation_notes=notes,
            )

            if not mok:
                node.status, node.error = "mutate_failed", merr
                self.archive.add(node)
                _log(f"[{variant_id}] mutation failed: {merr[:200]}")
                continue

            scope_err = enforce_allowed_paths(self.cfg, Path(parent.dir), variant_dir)
            if scope_err:
                node.status, node.error = "mutate_failed", scope_err
                self.archive.add(node)
                _log(f"[{variant_id}] rejected: {scope_err}")
                continue

            node.diff = diff_against_parent(Path(parent.dir), variant_dir)

            _log(f"[{variant_id}] evaluating...")
            eok, score, metrics, eerr = run_eval(self.cfg, variant_dir)
            if not eok:
                node.status, node.error = "eval_failed", eerr
                self.archive.add(node)
                _log(f"[{variant_id}] eval failed: {eerr[:200]}")
                continue

            node.status, node.score, node.metrics = "scored", score, metrics
            self.archive.add(node)
            better = " (NEW BEST)" if self.archive.best().id == node.id else ""
            _log(f"[{variant_id}] score={score}{better}")

        self.archive.cap(self.cfg.archive_size_cap)

    def run(self) -> Node:
        self.seed_root()
        for g in range(1, self.cfg.generations + 1):
            _log(f"=== generation {g}/{self.cfg.generations} ===")
            self.step(g)
            best = self.archive.best()
            _log(f"generation {g} done. best so far: {best.id} score={best.score}")
        return self.archive.best()
