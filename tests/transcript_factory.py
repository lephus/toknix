"""Builds Claude Code and Codex transcripts in the schema seen in real files (A6.1), for tests.

Claude Code: one assistant line per content block; the lines of one API request share message.id,
requestId and the same usage. Tool results come back in `user` lines. `system` lines with subtype
`compact_boundary` mark a compaction. Subagent requests carry isSidechain=true.

Since P8-T1 each subagent has its own file, `<session>/subagents/agent-<id>.jsonl` (`subagent_path`): every
line carries isSidechain=true and agentId, and the usage of a request's lines grows while it streams (only the
last line holds the full output_tokens).
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path


def iso(t: float) -> str:
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ClaudeTranscript:
    def __init__(self, session: str = "s1", t0: float = 1_800_000_000.0, model: str = "claude-opus-5-5",
                 agent: str | None = None):
        self.session, self.t, self.model, self.agent = session, t0, model, agent
        self.lines: list[str] = []
        self.n = 0

    def _line(self, obj: dict):
        if self.agent:  # a subagent file: every line is a sidechain of the parent session
            obj.update(isSidechain=True, agentId=self.agent)
        self.lines.append(json.dumps(obj, ensure_ascii=False))

    def user(self, text: str = "", tool_results: list[tuple[str, str]] = (), dt: float = 1.0, is_error=False):
        self.t += dt
        content = [{"type": "tool_result", "tool_use_id": tid, "content": body, "is_error": is_error}
                   for tid, body in tool_results]
        if text:
            content.append({"type": "text", "text": text})
        self._line({"type": "user", "sessionId": self.session, "isSidechain": False, "timestamp": iso(self.t),
                    "message": {"role": "user", "content": content}})

    def assistant(self, blocks: list[dict], input=10, cache_read=0, cache_write=0, output=50, dt: float = 1.0,
                  sidechain=False, model: str | None = None, cache_write_1h: int = 0, repeat_usage: bool = True,
                  partial_output: int | None = None):
        """One API request; each block becomes its own line, all with the same usage (like the real files).
        partial_output: output_tokens of every line but the last, as in subagent files (streamed usage)."""
        self.t += dt
        self.n += 1
        ident = f"{self.agent}_{self.n}" if self.agent else str(self.n)
        usage = {"input_tokens": input, "cache_creation_input_tokens": cache_write,
                 "cache_read_input_tokens": cache_read, "output_tokens": output,
                 "cache_creation": {"ephemeral_5m_input_tokens": cache_write - cache_write_1h,
                                    "ephemeral_1h_input_tokens": cache_write_1h}}
        blocks = blocks or [{"type": "text", "text": ""}]
        for i, b in enumerate(blocks):
            u = usage if partial_output is None or i == len(blocks) - 1 else dict(usage, output_tokens=partial_output)
            self._line({"type": "assistant", "sessionId": self.session, "isSidechain": sidechain,
                        "requestId": f"req_{ident}", "timestamp": iso(self.t),
                        "message": {"id": f"msg_{ident}", "type": "message", "role": "assistant",
                                    "model": model or self.model, "content": [b], "usage": u}})
            if not repeat_usage:
                break

    def compact(self):
        self.t += 1
        self._line({"type": "system", "subtype": "compact_boundary", "sessionId": self.session,
                    "timestamp": iso(self.t), "content": "Conversation compacted"})

    def junk(self, text: str = '{"type": "assistant", "message": {"id": '):
        self.lines.append(text)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return path


def subagent_path(session_file: Path, agent: str) -> Path:
    """Where Claude Code writes a subagent of the session in `session_file`."""
    return session_file.parent / session_file.stem / "subagents" / f"agent-{agent}.jsonl"


def tool_use(tid: str, name: str, **inp) -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def text(s: str) -> dict:
    return {"type": "text", "text": s}


def thinking(s: str = "") -> dict:  # "" = hidden, as in most real transcripts
    return {"type": "thinking", "thinking": s, "signature": "sig"}


class CodexRollout:
    """~/.codex/sessions/**/rollout-*.jsonl: session_meta, response_item function_call / _output, token_count."""

    def __init__(self, session: str = "c1", t0: float = 1_800_000_000.0):
        self.t = t0
        self.lines = [json.dumps({"timestamp": iso(t0), "type": "session_meta",
                                  "payload": {"id": session, "cwd": "/w", "originator": "codex_cli_rs"}})]
        self.total = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}

    def call(self, call_id: str, command: list[str], output: str):
        self.t += 1
        self.lines.append(json.dumps({"timestamp": iso(self.t), "type": "response_item", "payload": {
            "type": "function_call", "name": "shell", "call_id": call_id,
            "arguments": json.dumps({"command": command})}}))
        self.lines.append(json.dumps({"timestamp": iso(self.t), "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": call_id, "output": json.dumps({"output": output})}}))

    def turn(self, input_tokens: int, cached: int, output: int):
        self.t += 1
        last = {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output,
                "reasoning_output_tokens": 0, "total_tokens": input_tokens + output}
        for k in self.total:
            self.total[k] += last.get(k, 0)
        self.lines.append(json.dumps({"timestamp": iso(self.t), "type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_token_usage": dict(self.total, total_tokens=0),
                                            "last_token_usage": last}}}))

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return path
