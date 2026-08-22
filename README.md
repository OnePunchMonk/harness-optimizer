# harness-optimizer

Given an AI agent harness (system prompt, tool definitions, orchestration
code), evolve it: repeatedly mutate a working copy with an LLM agent, score
the result against your eval suite, and keep every result in an open archive
so the search can build on any promising variant, not just the current best.

## Design

The loop is closer to open-ended search than a classic generational GA:

- **Open archive, not truncation selection** — every evaluated variant is
  kept (up to `archive_size_cap`), including mediocre ones. A stepping-stone
  variant that scores worse than its parent can still unlock a better
  descendant later. This follows the **Darwin Gödel Machine** approach
  (Zhang et al., 2025, [arXiv:2505.22954](https://arxiv.org/abs/2505.22954)),
  which evolves a *population* of self-modifying coding agents rather than
  hill-climbing a single lineage, and found the open archive outperforms
  keep-best-only search.
- **Score-weighted + novelty-weighted parent sampling** — `parent_selection:
  score_weighted` prefers high-scoring parents but downweights nodes that
  have already been mutated many times, so the search explores breadth
  instead of tunneling into one branch. `uniform` and `best` (pure
  hill-climbing) are also available for comparison.
- **LLM as the mutation operator** — instead of random/genetic-programming
  edits, an agent (default: `claude -p` headless) reads the current variant
  and makes one targeted, explainable change per step. This is the same
  operator design as **AlphaEvolve** (DeepMind, 2025) for evolving code, and
  **Promptbreeder** / **EvoPrompt** for evolving prompts specifically —
  mutation prompts are rotated across a small set of strategies (bug-fix,
  simplify, restructure, exploratory) so successive edits aren't all the
  same kind of change.
- **Pluggable eval** — the tool doesn't grade anything itself. You provide
  `eval_cmd`, any command that takes a harness directory and prints
  `{"score": <float>, ...}` as its last line of JSON to stdout. That command
  is where you'd actually run the harness against a task suite (SWE-bench,
  a custom eval set, etc.) and grade the transcripts.

## Usage

```bash
pip install -e .
cp examples/config.example.yaml config.yaml
# edit config.yaml: harness_dir, eval_cmd, objective

harness-optimizer run config.yaml
harness-optimizer status config.yaml          # leaderboard
harness-optimizer show config.yaml <node_id>  # inspect a variant's diff
```

### Try it on the toy example

```bash
pip install -e .
harness-optimizer run examples/config.example.yaml --generations 3
```

This evolves `examples/toy_harness/SYSTEM_PROMPT.md` against
`examples/dummy_eval.py`, a scorer that rewards required keywords and
penalizes prompt length — just enough signal to exercise the whole loop
end-to-end without needing a real task suite or API calls beyond the
mutator itself.

## Config reference

See `examples/config.example.yaml` for all fields. Key ones:

| field | meaning |
|---|---|
| `harness_dir` | the harness codebase to evolve (copied, never modified in place) |
| `eval_cmd` | scores a candidate; `{harness_dir}` is substituted with its path |
| `objective` | free text folded into every mutation prompt — what "better" means |
| `parent_selection` | `score_weighted` (default), `uniform`, or `best` (hill-climb) |
| `mutator_cmd` | the agent invoked to edit each variant; must accept a prompt on stdin |
| `allowed_paths` | optional glob allowlist restricting what the mutator may touch |

## Running against a real target: openai/codex

`examples/config.codex.yaml` is a real, non-toy setup targeting
[openai/codex](https://github.com/openai/codex) (codex-rs), evolving its
actual production prompt files.

Two things make this harder than the toy example:

1. **codex-rs only speaks the model's Responses API** (`POST /v1/responses`,
   SSE, item-based streaming), not simple chat completions, and its base
   prompts are compiled in via `include_str!` — so evaluating a variant
   means a real `cargo build --release -p codex-cli` per candidate, not
   just an API call.
2. **No OpenAI or Anthropic API key required.** `shim/claude_responses_shim.py`
   is a small HTTP server that speaks just enough of the Responses API
   protocol (reverse-engineered from `codex-rs/codex-api/src/sse/responses.rs`)
   to satisfy codex, and answers each "model call" by shelling out to
   `claude -p --tools "" --json-schema ...` — using your existing Claude
   Code CLI auth, no key needed. Point a `model_providers` entry in
   `~/.codex/config.toml` at it (`base_url = "http://127.0.0.1:PORT/v1"`,
   `wire_api = "responses"`) — see the shim's module docstring for the
   full config snippet.

   This is an approximation, not a faithful model: `claude -p` is a full
   agent, not a bare completion endpoint, so we force single-shot
   structured output per turn (`--tools ""` disables its own tool use).
   Expect lower task fidelity than a real OpenAI model would get, and real
   dollar cost per turn (each codex model call becomes a `claude -p`
   subprocess call).

Setup:

```bash
git clone https://github.com/openai/codex.git targets/codex
cd targets/codex/codex-rs && cargo build --release -p codex-cli && cd -

harness-optimizer run examples/config.codex.yaml
```

`examples/codex_eval.py` is the `eval_cmd`: for each task under
`examples/codex_tasks/*/`, it rebuilds codex, starts the shim, runs
`codex exec <task prompt>` against a copy of the task repo, then grades by
running the task's test command. Score = fraction of tasks passing. The
included task suite has exactly one toy task (a one-line bug fix) — add
more `examples/codex_tasks/<name>/{task.json,repo/}` directories for a
real signal.

`allowed_paths` in `config.codex.yaml` restricts mutations to codex's
prompt/instruction files (`protocol/src/prompts/**`, `core/*_prompt.md`,
etc.) rather than the whole Rust codebase — enforced by the optimizer
(`mutate.py:enforce_allowed_paths`), which rejects any variant that
touched a file outside the allowlist.

## What this is not

It doesn't run your eval suite for you, doesn't sandbox the mutator or the
eval command (both execute with your permissions — review `mutator_cmd`
and `eval_cmd` before pointing this at anything sensitive), and doesn't do
crossover between variants (each mutation has exactly one parent). All
reasonable follow-ups, not yet built.
