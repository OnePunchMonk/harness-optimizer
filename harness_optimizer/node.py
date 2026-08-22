from __future__ import annotations

import dataclasses
import time
from typing import Optional


@dataclasses.dataclass
class Node:
    """One evaluated harness variant in the archive."""

    id: str
    parent_id: Optional[str]
    generation: int
    dir: str  # path to this variant's working copy
    diff: str  # unified diff vs parent (empty for root)
    mutation_prompt: str  # what we asked the mutator to do
    mutation_notes: str  # what the mutator agent reported doing

    score: Optional[float] = None
    metrics: dict = dataclasses.field(default_factory=dict)
    status: str = "pending"  # pending | scored | mutate_failed | eval_failed
    error: str = ""
    n_children: int = 0  # how many times this node has been sampled as a parent
    created_at: float = dataclasses.field(default_factory=time.time)

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Node":
        return Node(**d)
