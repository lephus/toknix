"""`tokopt statusline` (A7): one line for Claude Code's status bar, e.g.

    ctx 46% ▮▮▮▮▯▯▯▯▯▯ · 1.2M ETU · saved ~84k · /clear at 60%

Claude Code runs it after every turn with the session JSON on stdin, so it must be fast on a large
transcript: the file is read incrementally from the offset kept in ~/.tokopt/sl-<session>.json, and only
lines holding a usage are parsed. Like a hook, it never fails loudly: any error prints nothing, exit 0.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from tokopt.config import load, tokopt_home
from tokopt.textutil import fmt_num

RECENT_KEYS = 64  # requests whose lines may still arrive in a later read


def _cache_file(session: str) -> Path:
    return tokopt_home() / f"sl-{re.sub(r'[^A-Za-z0-9_-]', '_', session)[:80]}.json"


def _fresh(path: str) -> dict:
    return {"path": path, "offset": 0, "etu": 0.0, "ctx": 0, "start_ts": None, "keys": []}


def scan(path: Path, state: dict, weights: list[float]) -> dict:
    """Add the requests in the complete lines after state["offset"]; reset when the file was rewritten."""
    size = path.stat().st_size
    if state.get("path") != str(path) or size < state.get("offset", 0):
        state = _fresh(str(path))
    with open(path, "rb") as fh:
        fh.seek(state["offset"])
        data = fh.read()
    end = data.rfind(b"\n") + 1  # a half-written last line waits for the next call
    keys = list(state.get("keys", []))
    seen = set(keys)
    for raw in data[:end].split(b"\n"):
        if b'"usage"' not in raw:
            if state["start_ts"] is None and b'"timestamp"' in raw:
                m = re.search(rb'"timestamp":\s*"([^"]+)"', raw)
                if m:
                    state["start_ts"] = _ts(m.group(1).decode())
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if obj.get("type") != "assistant" or not isinstance(usage, dict):
            continue
        if state["start_ts"] is None:
            state["start_ts"] = _ts(obj.get("timestamp"))
        key = f"{msg.get('id')}|{obj.get('requestId')}"
        if key in seen:  # another chunk of a request already counted
            continue
        seen.add(key)
        keys.append(key)
        u = [usage.get(k) if isinstance(usage.get(k), int) else 0 for k in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")]
        state["etu"] += sum(w * x for w, x in zip(weights, u))
        if obj.get("isSidechain") is not True:
            state["ctx"] = u[0] + u[1] + u[2]
    state["offset"] += end
    state["keys"] = keys[-RECENT_KEYS:]
    return state


def _ts(value) -> float | None:
    from tokopt.transcripts import parse_ts
    return parse_ts(value)


def saved_tokens(session: str, since: float | None) -> int:
    """Direct savings of this session: filter runs since it started + its read-guard denials (no re-read factor)."""
    db = tokopt_home() / "tokopt.db"
    if not db.exists():
        return 0
    from tokopt.measure import _saved_tokens, report_ratios
    ratios = report_ratios(load())
    con = sqlite3.connect(str(db), timeout=1)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
        total = 0.0
        if {"saved_default", "saved_hex", "saved_non_ascii"} <= cols:
            for row in con.execute("SELECT raw_chars, out_chars, saved_default, saved_hex, saved_non_ascii FROM runs "
                                   "WHERE ts >= ?", (since or 0,)):
                total += _saved_tokens(row, ratios)
        total += con.execute("SELECT COALESCE(SUM(est_tokens), 0) FROM guard WHERE session = ?",
                             (session,)).fetchone()[0]
        return int(total)
    except sqlite3.Error:
        return 0
    finally:
        con.close()


WINDOWS = (200_000, 1_000_000)


def context_window(model: dict, ctx: int, configured: int) -> int:
    """The configured window, unless the model says 1M (`…[1m]`, "1M context"), or the context already
    used is larger: then the smallest standard window that holds it (a context never exceeds its window)."""
    ident = f"{model.get('id', '')} {model.get('display_name', '')}" if isinstance(model, dict) else ""
    if re.search(r"\[1m\]|\b1M\b", ident, re.I):
        return max(configured, 1_000_000)
    if ctx > configured:
        return next((w for w in WINDOWS if w >= ctx), ctx)
    return configured


def render(state: dict, session: str, cfg: dict, model: dict | None = None) -> str:
    window = context_window(model or {}, state["ctx"], cfg["context_window"])
    pct = state["ctx"] / window if window else 0.0
    filled = min(int(pct * 10), 10)
    head = f"ctx {pct:.0%} {'▮' * filled}{'▯' * (10 - filled)}"
    if not os.environ.get("NO_COLOR"):
        if pct >= cfg["statusline_alert"]:
            head = f"\x1b[31m{head}\x1b[0m"
        elif pct >= cfg["statusline_warn"]:
            head = f"\x1b[33m{head}\x1b[0m"
    saved = saved_tokens(session, state.get("start_ts"))
    return (f"{head} · {fmt_num(state['etu'])} ETU · saved ~{fmt_num(saved)} · "
            f"/clear at {cfg['statusline_warn']:.0%}")


def main() -> int:
    try:
        raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8")
        data = json.loads(raw.decode("utf-8") or "{}")
        path = Path(data["transcript_path"]).expanduser()
        session = str(data.get("session_id") or path.stem)
        cfg = load()
        cache = _cache_file(session)
        try:
            state = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = _fresh(str(path))
        state = scan(path, state, [float(x) for x in cfg["weights"]])
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass
        line = render(state, session, cfg, data.get("model")) + "\n"
        out = getattr(sys.stdout, "buffer", None)
        if out is not None:
            out.write(line.encode("utf-8"))
            out.flush()
        else:
            sys.stdout.write(line)
    except Exception:  # the status bar never shows a traceback
        if os.environ.get("TOKOPT_DEBUG") == "1":
            import traceback
            traceback.print_exc(file=sys.stderr)
    return 0
