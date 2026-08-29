from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Optional

from .node import Node


class Archive:
    """
    Open, growing archive of every evaluated variant (Darwin Godel Machine
    style: https://arxiv.org/abs/2505.22954). We never discard nodes just for
    scoring low -- a mediocre-scoring mutation can still be a useful stepping
    stone -- we only cap total size (evicting lowest-value nodes) if configured.
    """

    def __init__(self, path: Path):
        self.path = path
        self.nodes: dict[str, Node] = {}
        if path.exists():
            data = json.loads(path.read_text())
            self.nodes = {k: Node.from_json(v) for k, v in data.items()}

    def add(self, node: Node) -> None:
        self.nodes[node.id] = node
        self.save()

    def get(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({k: v.to_json() for k, v in self.nodes.items()}, indent=2))
        tmp.replace(self.path)

    def scored(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.status == "scored" and n.score is not None]

    def best(self) -> Optional[Node]:
        s = self.scored()
        return max(s, key=lambda n: n.score) if s else None

    def cap(self, max_size: int) -> None:
        if max_size <= 0 or len(self.nodes) <= max_size:
            return
        # Keep the best-scoring nodes plus the root(s); drop the rest.
        ranked = sorted(self.scored(), key=lambda n: n.score, reverse=True)
        keep_ids = {n.id for n in ranked[:max_size]}
        keep_ids |= {n.id for n in self.nodes.values() if n.parent_id is None}
        self.nodes = {k: v for k, v in self.nodes.items() if k in keep_ids}
        self.save()

    def sample_parent(self, strategy: str) -> Node:
        candidates = self.scored()
        if not candidates:
            raise RuntimeError("no scored nodes to sample a parent from")

        if strategy == "best":
            return self.best()

        if strategy == "uniform":
            return random.choice(candidates)

        if strategy == "score_weighted":
            # DGM-style: favor high score, but downweight nodes that have
            # already spawned many children so the search doesn't tunnel
            # into one lineage.
            scores = [n.score for n in candidates]
            lo, hi = min(scores), max(scores)
            spread = hi - lo or 1.0
            weights = []
            for n in candidates:
                norm = (n.score - lo) / spread  # 0..1
                fitness_w = math.exp(3 * norm)  # softmax-ish preference for higher score
                novelty_w = 1.0 / (1 + n.n_children)  # explore under-tried nodes
                weights.append(fitness_w * novelty_w)
            chosen = random.choices(candidates, weights=weights, k=1)[0]
            return chosen

        raise ValueError(f"unknown parent_selection strategy: {strategy}")
