#!/usr/bin/env python3
"""
An OpenAI Responses-API-compatible HTTP server that answers codex-rs's model
calls by shelling out to `claude -p` (Claude Code CLI, subscription auth --
no API key required) instead of a real OpenAI model.

codex-rs (https://github.com/openai/codex) only speaks the Responses API:
POST /v1/responses, SSE-streamed, item-based (response.created ->
response.output_item.done* -> response.completed). See
codex-rs/codex-api/src/sse/responses.rs for the event parser this was
reverse-engineered against.

Point codex at this shim via config.toml:

    [model_providers.claude-shim]
    name = "claude-shim"
    base_url = "http://127.0.0.1:PORT/v1"
    wire_api = "responses"

    [profiles.claude]
    model_provider = "claude-shim"
    model = "claude-shim"   # name is arbitrary, ignored by the shim

    codex -p claude exec "..."

Caveats (see README): claude -p is itself a full agent, not a bare
completion endpoint. We force it into "pick one action" mode with
--tools "" (disables its own tool execution) and --json-schema (structured
output), asking it to choose a single codex tool call or produce a final
text message per turn. This is an approximation of a real model's behavior,
not a faithful reproduction -- expect lower task-completion fidelity than
codex would get from an actual OpenAI model, and expect real dollar cost
per turn (each codex "model call" is a full claude -p invocation).
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLAUDE_CMD = "claude"
CLAUDE_TIMEOUT_S = 300
LOG = sys.stderr


def log(msg: str) -> None:
    print(f"[shim {time.strftime('%H:%M:%S')}] {msg}", file=LOG, flush=True)


# ---- translate codex's Responses API request into a claude -p prompt -----

def content_text(content_items) -> str:
    parts = []
    for c in content_items or []:
        if not isinstance(c, dict):
            continue
        t = c.get("text")
        if t:
            parts.append(t)
    return "\n".join(parts)


def render_input_item(item: dict) -> str:
    kind = item.get("type")
    if kind == "message":
        role = item.get("role", "user")
        text = content_text(item.get("content"))
        return f"[{role}] {text}"
    if kind == "function_call":
        return f"[assistant called tool] {item.get('name')}({item.get('arguments')}) call_id={item.get('call_id')}"
    if kind == "function_call_output":
        output = item.get("output")
        if isinstance(output, dict):
            body = output.get("body")
            text = body if isinstance(body, str) else json.dumps(body)
        else:
            text = str(output)
        return f"[tool result for {item.get('call_id')}] {text[:4000]}"
    if kind == "reasoning":
        return ""  # don't replay internal reasoning back into the prompt
    return f"[{kind}] {json.dumps(item)[:2000]}"


def render_tools(tools: list) -> str:
    if not tools:
        return "(no tools available this turn)"
    lines = []
    for t in tools:
        name = t.get("name", "?")
        desc = (t.get("description") or "").strip().splitlines()[0:1]
        desc = desc[0] if desc else ""
        params = json.dumps(t.get("parameters", {}))
        lines.append(f"- {name}: {desc}\n  parameters schema: {params}")
    return "\n".join(lines)


def build_prompt(body: dict) -> str:
    instructions = body.get("instructions") or ""
    input_items = body.get("input") or []
    tools = body.get("tools") or []

    transcript = "\n".join(filter(None, (render_input_item(i) for i in input_items)))

    return f"""{instructions}

--- Available tools for this turn ---
{render_tools(tools)}

--- Conversation so far ---
{transcript}

--- Your task ---
Decide the single next action as the assistant. Either call exactly one tool
(pick the most appropriate one for the current step) or, if the task is
already complete, respond with a final message and no tool call. Respond
ONLY via the structured schema provided; do not use any of your own tools.
"""


def tool_call_schema(tools: list) -> dict:
    names = [t.get("name") for t in tools if t.get("name")]
    name_schema = {"type": "string", "enum": names} if names else {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": name_schema,
                        "arguments_json": {
                            "type": "string",
                            "description": "JSON-encoded arguments object for the tool call",
                        },
                    },
                    "required": ["name", "arguments_json"],
                },
            },
            "final_message": {
                "type": ["string", "null"],
                "description": "Final answer to the user; set only when no tool call is needed",
            },
        },
        "required": ["tool_calls", "final_message"],
    }


def call_claude(prompt: str, schema: dict, model: str | None) -> dict:
    cmd = [
        CLAUDE_CMD, "-p", prompt,
        "--tools", "",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--setting-sources", "",
    ]
    if model and not model.startswith("claude-shim"):
        cmd += ["--model", model]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[-2000:]}")

    out = json.loads(proc.stdout)
    structured = out.get("structured_output")
    if structured is None:
        raise RuntimeError(f"claude did not return structured_output: {proc.stdout[-2000:]}")
    return structured


# ---- build codex's expected Responses API SSE stream ----------------------

def sse_event(kind: str, data: dict) -> bytes:
    payload = {"type": kind, **data}
    return f"event: {kind}\ndata: {json.dumps(payload)}\n\n".encode()


def build_response_stream(structured: dict) -> bytes:
    chunks = [sse_event("response.created", {"response": {}})]

    tool_calls = structured.get("tool_calls") or []
    final_message = structured.get("final_message")

    for call in tool_calls:
        item = {
            "type": "function_call",
            "name": call.get("name", ""),
            "arguments": call.get("arguments_json", "{}"),
            "call_id": f"call_{uuid.uuid4().hex[:16]}",
        }
        chunks.append(sse_event("response.output_item.done", {"item": item}))

    if not tool_calls and final_message is not None:
        item = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": final_message}],
        }
        chunks.append(sse_event("response.output_item.done", {"item": item}))

    resp_id = f"resp_{uuid.uuid4().hex}"
    chunks.append(sse_event("response.completed", {"response": {"id": resp_id}}))
    return b"".join(chunks)


def build_error_stream(message: str) -> bytes:
    resp_id = f"resp_{uuid.uuid4().hex}"
    return sse_event("response.failed", {
        "response": {"id": resp_id, "error": {"code": "shim_error", "message": message}}
    })


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(fmt % args)

    def do_GET(self):
        if self.path == "/shutdown":
            self.send_response(200)
            self.end_headers()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not self.path.startswith("/v1/responses"):
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as e:
            self._respond_sse(build_error_stream(f"invalid request JSON: {e}"))
            return

        n_tools = len(body.get("tools") or [])
        n_input = len(body.get("input") or [])
        log(f"turn: model={body.get('model')} input_items={n_input} tools={n_tools}")

        try:
            prompt = build_prompt(body)
            schema = tool_call_schema(body.get("tools") or [])
            structured = call_claude(prompt, schema, body.get("model"))
            stream = build_response_stream(structured)
        except Exception as e:
            log(f"turn failed: {e}")
            self._respond_sse(build_error_stream(str(e)))
            return

        self._respond_sse(stream)

    def _respond_sse(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=60123)
    args = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log(f"listening on http://127.0.0.1:{args.port}  (POST /v1/responses)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
