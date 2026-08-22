#!/usr/bin/env python3
"""
Minimal example eval_cmd target. Scores a "harness" directory by how many
of a fixed set of required keywords appear in its SYSTEM_PROMPT.md, penalized
by file length (a stand-in for token cost). Real eval_cmd scripts should
actually run the harness against tasks and grade transcripts.

Usage: dummy_eval.py --harness-dir PATH
Prints one JSON line: {"score": <float>, ...extra metrics}
"""
import argparse
import json
from pathlib import Path

REQUIRED_KEYWORDS = ["plan", "verify", "test", "concise", "tool"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness-dir", required=True)
    args = ap.parse_args()

    prompt_path = Path(args.harness_dir) / "SYSTEM_PROMPT.md"
    text = prompt_path.read_text().lower() if prompt_path.exists() else ""

    hits = sum(1 for kw in REQUIRED_KEYWORDS if kw in text)
    length_penalty = len(text) / 2000.0
    score = hits - length_penalty

    print(json.dumps({
        "score": score,
        "keyword_hits": hits,
        "prompt_chars": len(text),
    }))


if __name__ == "__main__":
    main()
