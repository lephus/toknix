"""Agent transcripts (A6.1): Claude Code `~/.claude/projects/*/*.jsonl`, Codex `~/.codex/sessions/**/rollout-*.jsonl`.

Real Claude Code files write one assistant line per content block, and every line of one API request
repeats the usage: requests are keyed by (message.id, requestId), their blocks merged, their usage
counted once. Each subagent has its own file, `<session>/subagents/agent-<id>.jsonl`, read into the same
session as a chain of its own; there the usage grows while a request streams, so the largest one is kept. Everything is read defensively: a broken line, a missing field or a field of the wrong
type is skipped, never fatal.
"""
from __future__ import annotations

import datetime
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from toknix.config import claude_dir, codex_dir

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


@dataclass
class ToolResult:
    tool_use_id: str
    text: str
    is_error: bool = False


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict
    result: ToolResult | None = None


@dataclass
class Request:
    key: tuple
    ts: float | None
    model: str
    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0
    cache_write_1h: int = 0
    sidechain: bool = False
    chain: str = ""                    # "" main, "agent-<id>" a subagent file, "sidechain" inline in the session file
    text_chars: int = 0
    thinking_chars: int = 0
    thinking_hidden: bool = False      # a thinking block whose text is not in the transcript
    write_input_chars: int = 0         # input of Write/Edit/MultiEdit/NotebookEdit
    other_input_chars: int = 0         # input of the other tools
    tool_uses: list = field(default_factory=list)
    compacted_before: bool = False     # a compaction boundary since the previous request of its chain
    results_before: list = field(default_factory=list)  # tool results sent with this request
    user_text_before: int = 0          # chars of user text sent with this request

    @property
    def context(self) -> int:
        return self.input + self.cache_read + self.cache_write


@dataclass
class Session:
    agent: str
    path: Path
    id: str
    requests: list = field(default_factory=list)
    compactions: int = 0

    @property
    def main(self) -> list:
        """Requests of the session itself: subagent (sidechain) requests left out (A6.2)."""
        return [r for r in self.requests if not r.sidechain]

    @property
    def chains(self) -> list:
        """Request lists, one per chain in order of appearance; the main chain first, even when empty."""
        out: dict[str, list] = {"": []}
        for r in self.requests:
            out.setdefault(r.chain, []).append(r)
        return list(out.values())

    def totals(self, main_only: bool = False) -> dict:
        rs = self.main if main_only else self.requests
        return {k: sum(getattr(r, k) for r in rs) for k in ("input", "cache_write", "cache_read", "output")}

    @property
    def last_ts(self) -> float | None:
        stamps = [r.ts for r in self.requests if r.ts]
        return max(stamps) if stamps else None

    @property
    def first_ts(self) -> float | None:
        stamps = [r.ts for r in self.requests if r.ts]
        return min(stamps) if stamps else None


# --------------------------------------------------------------------------- helpers
def parse_ts(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _lines(path: Path):
    """Parsed JSON objects of a JSONL file; unreadable files and broken lines yield nothing."""
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except (OSError, UnicodeDecodeError):
        return


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


# --------------------------------------------------------------------------- Claude Code
USAGE_FIELDS = (("input", "input_tokens"), ("cache_write", "cache_creation_input_tokens"),
                ("cache_read", "cache_read_input_tokens"), ("output", "output_tokens"))


def parse_claude(path) -> Session:
    """The session file, then its subagent files; request keys and tool uses are shared by all of them."""
    path = Path(path)
    session = Session("claude", path, path.stem)
    by_key: dict[tuple, Request] = {}
    uses: dict[str, ToolUse] = {}
    _read_claude(path, session, by_key, uses, None)
    for sub in sorted((path.parent / path.stem / "subagents").glob("*.jsonl")):
        _read_claude(sub, session, by_key, uses, sub.stem)
    return session


def _read_claude(path: Path, session: Session, by_key: dict, uses: dict, agent: str | None):
    """agent: the subagent file's name (`agent-<id>`), None for the session file."""
    pending = {False: [[], 0, False], True: [[], 0, False]}  # per chain: results, user text chars, compacted
    mine: set = set()  # keys of the requests this file brought in

    for obj in _lines(path):
        kind = obj.get("type")
        side = agent is not None or obj.get("isSidechain") is True
        if agent is None and isinstance(obj.get("sessionId"), str):
            session.id = obj["sessionId"]
        if kind == "system" and obj.get("subtype") == "compact_boundary":
            if not side:
                session.compactions += 1
            pending[side][2] = True
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if kind == "user":
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result" and isinstance(b.get("tool_use_id"), str):
                    res = ToolResult(b["tool_use_id"], _result_text(b.get("content")), b.get("is_error") is True)
                    pending[side][0].append(res)
                    if res.tool_use_id in uses:
                        uses[res.tool_use_id].result = res
                elif b.get("type") == "text" and isinstance(b.get("text"), str):
                    pending[side][1] += len(b["text"])
            if isinstance(content, str):
                pending[side][1] += len(content)
            continue
        if kind != "assistant":
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict) or not isinstance(msg.get("id"), str) or not any(
                _int(usage.get(k)) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                              "cache_creation_input_tokens")):
            continue
        key = (msg["id"], obj.get("requestId"))
        r = by_key.get(key)
        if r is None:
            cache_creation = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
            r = Request(key, parse_ts(obj.get("timestamp")), msg.get("model") if isinstance(msg.get("model"), str)
                        else "", _int(usage.get("input_tokens")), _int(usage.get("cache_creation_input_tokens")),
                        _int(usage.get("cache_read_input_tokens")), _int(usage.get("output_tokens")),
                        _int(cache_creation.get("ephemeral_1h_input_tokens")), side,
                        agent or ("sidechain" if side else ""))
            results, user_text, compacted = pending[side]
            r.results_before, r.user_text_before, r.compacted_before = results, user_text, compacted
            pending[side] = [[], 0, False]
            by_key[key] = r
            mine.add(key)
            session.requests.append(r)
        elif key not in mine:
            continue  # already read from the session file (older Claude Code): count it once
        else:  # a later line of the same request: subagent files stream a growing usage
            for name, k in USAGE_FIELDS:
                setattr(r, name, max(getattr(r, name), _int(usage.get(k))))
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and isinstance(b.get("text"), str):
                r.text_chars += len(b["text"])
            elif t == "thinking":
                thought = b.get("thinking") if isinstance(b.get("thinking"), str) else ""
                r.thinking_chars += len(thought)
                r.thinking_hidden = r.thinking_hidden or not thought.strip()
            elif t == "tool_use" and isinstance(b.get("id"), str):
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                use = ToolUse(b["id"], b.get("name") if isinstance(b.get("name"), str) else "", inp)
                uses[use.id] = use
                r.tool_uses.append(use)
                size = len(json.dumps(inp, ensure_ascii=False))
                if use.name in WRITE_TOOLS:
                    r.write_input_chars += size
                else:
                    r.other_input_chars += size


# --------------------------------------------------------------------------- Codex
def parse_codex(path) -> Session:
    """One request per token_count event (its last_token_usage); real input = input − cached input.
    function_call items are attached to the next request, their output joined by call_id."""
    path = Path(path)
    session = Session("codex", path, path.stem)
    calls: dict[str, ToolUse] = {}
    waiting: list[ToolUse] = []
    for obj in _lines(path):
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if obj.get("type") == "session_meta" and isinstance(payload.get("id"), str):
            session.id = payload["id"]
        elif kind == "function_call" and isinstance(payload.get("call_id"), str):
            try:
                args = json.loads(payload.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            use = ToolUse(payload["call_id"], payload.get("name") if isinstance(payload.get("name"), str) else "",
                          args if isinstance(args, dict) else {})
            calls[use.id] = use
            waiting.append(use)
        elif kind == "function_call_output" and payload.get("call_id") in calls:
            out = payload.get("output")
            try:
                parsed = json.loads(out) if isinstance(out, str) else out
                out = parsed.get("output", out) if isinstance(parsed, dict) else out
            except ValueError:
                pass
            calls[payload["call_id"]].result = ToolResult(payload["call_id"], out if isinstance(out, str) else "")
        elif kind == "token_count":
            info = payload.get("info")
            last = info.get("last_token_usage") if isinstance(info, dict) else None
            if not isinstance(last, dict):
                continue
            cached = _int(last.get("cached_input_tokens"))
            r = Request(("codex", len(session.requests)), parse_ts(obj.get("timestamp")), "",
                        max(_int(last.get("input_tokens")) - cached, 0), 0, cached, _int(last.get("output_tokens")))
            r.tool_uses, waiting = waiting, []
            session.requests.append(r)
    return session


# --------------------------------------------------------------------------- listing
def transcript_files(agents=("claude", "codex")) -> list[tuple[str, Path]]:
    out = []
    if "claude" in agents:
        out += [("claude", p) for p in sorted((claude_dir() / "projects").glob("*/*.jsonl"))]
    if "codex" in agents:
        out += [("codex", p) for p in sorted((codex_dir() / "sessions").rglob("rollout-*.jsonl"))]
    return out


def sessions(days: int | None = None, agents=("claude", "codex")) -> list[Session]:
    """Sessions with at least one request, active in the last `days` days (all when None)."""
    since = time.time() - days * 86400 if days else None
    out = []
    for agent, path in transcript_files(agents):
        if since is not None:
            try:
                if path.stat().st_mtime < since - 86400:  # cheap skip; timestamps decide below
                    continue
            except OSError:
                continue
        s = parse_claude(path) if agent == "claude" else parse_codex(path)
        if not s.requests:
            continue
        if since is not None and (s.last_ts or 0) < since:
            continue
        out.append(s)
    return out
