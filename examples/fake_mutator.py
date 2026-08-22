#!/usr/bin/env python3
"""Fake mutator for smoke-testing the loop: appends a keyword to SYSTEM_PROMPT.md."""
import random
import sys
from pathlib import Path

KEYWORDS = ["plan", "verify", "test", "concise", "tool"]

p = Path("SYSTEM_PROMPT.md")
text = p.read_text() if p.exists() else ""
kw = random.choice(KEYWORDS)
p.write_text(text + f"\nAlways {kw} your work.\n")
Path("MUTATION_NOTES.md").write_text(f"Added guidance to {kw}.")
sys.exit(0)
