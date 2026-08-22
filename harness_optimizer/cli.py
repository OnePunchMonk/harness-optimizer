from __future__ import annotations

import argparse
import json
from pathlib import Path

from .archive import Archive
from .config import Config
from .optimizer import Optimizer


def cmd_run(args: argparse.Namespace) -> None:
    cfg = Config.load(args.config)
    if args.generations is not None:
        cfg.generations = args.generations
    opt = Optimizer(cfg)
    best = opt.run()
    print("\n=== DONE ===")
    print(f"best variant: {best.id}  score={best.score}")
    print(f"dir: {best.dir}")


def cmd_status(args: argparse.Namespace) -> None:
    cfg = Config.load(args.config)
    archive = Archive(cfg.work_dir / "archive.json")
    scored = sorted(archive.scored(), key=lambda n: n.score, reverse=True)
    print(f"{len(archive.nodes)} total nodes, {len(scored)} scored\n")
    for n in scored[: args.top]:
        print(f"{n.id:16s} gen={n.generation:<3} score={n.score:<10.4f} parent={n.parent_id} children_spawned={n.n_children}")


def cmd_show(args: argparse.Namespace) -> None:
    cfg = Config.load(args.config)
    archive = Archive(cfg.work_dir / "archive.json")
    n = archive.get(args.node_id)
    print(json.dumps(n.to_json(), indent=2)[:4000])
    if n.diff:
        print("\n--- diff vs parent ---")
        print(n.diff)


def main() -> None:
    p = argparse.ArgumentParser(prog="harness-optimizer", description="Evolve an agent harness via mutate+eval search.")
    sub = p.add_subparsers(required=True)

    p_run = sub.add_parser("run", help="run the evolutionary search")
    p_run.add_argument("config")
    p_run.add_argument("--generations", type=int, default=None)
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="show archive leaderboard")
    p_status.add_argument("config")
    p_status.add_argument("--top", type=int, default=15)
    p_status.set_defaults(func=cmd_status)

    p_show = sub.add_parser("show", help="show one node's details/diff")
    p_show.add_argument("config")
    p_show.add_argument("node_id")
    p_show.set_defaults(func=cmd_show)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
