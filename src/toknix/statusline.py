"""`toknix statusline` (A7): one line for Claude Code's status bar, e.g.

    ctx 92k/1.0M ▯▯▯▯▯▯▯▯▯▯ · 1.2M ETU · saved ~84k ETU (6.5%) · /clear at 600k

Claude Code runs it after every turn with the session JSON on stdin, so it must be fast on a large
transcript: the file, and each subagent file of the session, is read incrementally from the offset kept in
~/.toknix/sl-<session>.json, and only lines holding a usage are parsed. Like a hook, it never fails loudly: any error prints nothing, exit 0.
"""
from __future__ import annotations

import bisect
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from toknix.config import load, toknix_home
from toknix.pricing import Pricer
from toknix.textutil import fmt_num

RECENT_KEYS = 64  # requests whose lines may still arrive in a later read
STATE_VERSION = 2  # P8-T2: usage of 5 kinds, each request at its model's price; older caches are read again


def _cache_file(session: str) -> Path:
    return toknix_home() / f"sl-{re.sub(r'[^A-Za-z0-9_-]', '_', session)[:80]}.json"


def _fresh(path: str) -> dict:
    return {"v": STATE_VERSION, "path": path, "offset": 0, "etu": 0.0, "ctx": 0, "start_ts": None, "keys": {},
            "req_ts": [], "compact_ts": [],  # main-chain request times and compactions: saved ETU (P7-T1)
            "subs": {},  # P8-T1: subagent file name -> its own offset, keys and ETU
            "main_w": None, "one_hour": False}  # P8-T2: weights of the last main request, 1h writes seen


def _fresh_sub() -> dict:
    return {"offset": 0, "etu": 0.0, "keys": {}}


def scan(path: Path, state: dict, pr: Pricer) -> dict:
    """Add the requests in the complete lines after the saved offsets of the session file and of its subagent
    files (`<session>/subagents/*.jsonl`); a file shorter than its offset is read again from the start."""
    size = path.stat().st_size
    if state.get("path") != str(path) or size < state.get("offset", 0) or "subs" not in state \
            or state.get("v") != STATE_VERSION:
        state = _fresh(str(path))
    _scan_file(path, state, pr, state)
    subs = {}
    for sub in sorted((path.parent / path.stem / "subagents").glob("*.jsonl")):
        sub_state = state["subs"].get(sub.name) or _fresh_sub()
        try:
            if sub.stat().st_size < sub_state["offset"]:
                sub_state = _fresh_sub()
            if sub.stat().st_size > sub_state["offset"]:
                _scan_file(sub, sub_state, pr, None)
        except OSError:
            pass
        subs[sub.name] = sub_state
    state["subs"] = subs
    return state


def total_etu(state: dict) -> float:
    return state["etu"] + sum(sub["etu"] for sub in state.get("subs", {}).values())


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def _usage(usage: dict) -> list[int]:
    """input, 5-minute cache write, 1-hour cache write, cache read, output (A1)."""
    def num(v):
        return v if isinstance(v, int) and not isinstance(v, bool) else 0
    cc = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    write = num(usage.get("cache_creation_input_tokens"))
    one_hour = min(num(cc.get("ephemeral_1h_input_tokens")), write)
    return [num(usage.get("input_tokens")), write - one_hour, one_hour, num(usage.get("cache_read_input_tokens")),
            num(usage.get("output_tokens"))]


def _scan_file(path: Path, fstate: dict, pr: Pricer, main: dict | None):
    """Read one file from fstate["offset"]; main: the session state for the session file (ctx, request times,
    compactions), None for a subagent file, which only adds ETU."""
    with open(path, "rb") as fh:
        fh.seek(fstate["offset"])
        data = fh.read()
    end = data.rfind(b"\n") + 1  # a half-written last line waits for the next call
    keys = dict(fstate.get("keys", {}))  # recent request key -> usage already counted
    for raw in data[:end].split(b"\n"):
        if b'"usage"' not in raw:
            if main is None:
                continue
            if b'"compact_boundary"' in raw:
                try:
                    obj = json.loads(raw)
                except ValueError:
                    obj = None
                ts = _ts(obj.get("timestamp")) if isinstance(obj, dict) and obj.get("subtype") == "compact_boundary" \
                    else None
                if ts is not None:
                    main["compact_ts"].append(ts)
            if main["start_ts"] is None and b'"timestamp"' in raw:
                m = re.search(rb'"timestamp":\s*"([^"]+)"', raw)
                if m:
                    main["start_ts"] = _ts(m.group(1).decode())
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if obj.get("type") != "assistant" or not isinstance(usage, dict):
            continue
        if main is not None and main["start_ts"] is None:
            main["start_ts"] = _ts(obj.get("timestamp"))
        key = f"{msg.get('id')}|{obj.get('requestId')}"
        u = _usage(usage)
        weights = pr.weights(_str(msg.get("model")), u[0] + u[1] + u[2] + u[3], _str(usage.get("speed")),
                             _str(usage.get("inference_geo")))
        if key in keys:  # another line of a request already counted: add only what grew (streamed usage)
            before = keys.pop(key)
            fstate["etu"] += sum(w * max(x - b, 0) for w, x, b in zip(weights, u, before))
            keys[key] = [max(x, b) for x, b in zip(u, before)]
            continue
        keys[key] = u
        fstate["etu"] += sum(w * x for w, x in zip(weights, u))
        if main is not None and obj.get("isSidechain") is not True:
            main["ctx"] = u[0] + u[1] + u[2] + u[3]
            main["main_w"] = weights
            main["one_hour"] = main.get("one_hour") or u[2] > 0
            ts = _ts(obj.get("timestamp"))
            if ts is not None:
                main["req_ts"].append(ts)
    fstate["offset"] += end
    fstate["keys"] = dict(list(keys.items())[-RECENT_KEYS:])


def _ts(value) -> float | None:
    from toknix.transcripts import parse_ts
    return parse_ts(value)


def saved_events(session: str, since: float | None) -> list[tuple[float, float]]:
    """(ts, tokens saved directly) of this session: filter runs since it started + its read-guard denials."""
    db = toknix_home() / "toknix.db"
    if not db.exists():
        return []
    from toknix.measure import _saved_tokens, report_ratios
    ratios = report_ratios(load())
    con = sqlite3.connect(str(db), timeout=1)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
        events = []
        if {"saved_default", "saved_hex", "saved_non_ascii"} <= cols:
            for row in con.execute("SELECT ts, raw_chars, out_chars, saved_default, saved_hex, saved_non_ascii "
                                   "FROM runs WHERE ts >= ?", (since or 0,)):
                events.append((row[0], _saved_tokens(row[1:], ratios)))
        events += con.execute("SELECT ts, est_tokens FROM guard WHERE session = ?", (session,)).fetchall()
        return events
    except sqlite3.Error:
        return []
    finally:
        con.close()


def saved_etu(events: list, req_ts: list[float], compact_ts: list[float], weights: list[float]) -> float:
    """ETU each saving has avoided so far (A7): the first main request after it would have written it to the
    cache, each later one read it again, up to the next compaction. weights: [w_cw, w_cr]."""
    reqs, compacts = sorted(req_ts), sorted(compact_ts)
    total = 0.0
    for ts, tokens in events:
        c = bisect.bisect_right(compacts, ts)
        stop = compacts[c] if c < len(compacts) else float("inf")
        n = bisect.bisect_left(reqs, stop) - bisect.bisect_right(reqs, ts)
        if n > 0:
            total += tokens * (weights[0] + weights[1] * (n - 1))
    return total


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


def fmt_ctx(n: float) -> str:
    """A context size, short: 2.0k, 92k, 1.2M."""
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f}M"
    return f"{n / 1e3:.0f}k" if abs(n) >= 1e4 else fmt_num(n)


def last_request(path: Path, tail: int = 1 << 20) -> tuple[int, str, str, str] | None:
    """(context, model, speed, inference geo) of the last main-chain request, from the last `tail` bytes of the
    transcript (A7, hook prompt)."""
    with open(path, "rb") as fh:
        fh.seek(max(0, fh.seek(0, os.SEEK_END) - tail))
        data = fh.read()
    for raw in reversed(data.split(b"\n")):
        if b'"usage"' not in raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if obj.get("type") != "assistant" or not isinstance(usage, dict) or obj.get("isSidechain") is True:
            continue
        return (sum(_usage(usage)[:4]), _str(msg.get("model")), _str(usage.get("speed")),
                _str(usage.get("inference_geo")))
    return None


def render(state: dict, session: str, cfg: dict, model: dict | None = None) -> str:
    window = context_window(model or {}, state["ctx"], cfg["context_window"])
    budget = min(window, cfg["context_budget"])  # P7-T2: a 1M window is not a reason to re-read 1M per request
    pct = state["ctx"] / budget if budget else 0.0
    filled = min(int(pct * 10), 10)
    head = f"ctx {fmt_ctx(state['ctx'])}/{fmt_ctx(budget)} {'▮' * filled}{'▯' * (10 - filled)}"
    if not os.environ.get("NO_COLOR"):
        if pct >= cfg["statusline_alert"]:
            head = f"\x1b[31m{head}\x1b[0m"
        elif pct >= cfg["statusline_warn"]:
            head = f"\x1b[33m{head}\x1b[0m"
    w = state.get("main_w") or Pricer(cfg).weights("")  # P8-T2: the main chain's model, `weights` before any
    saved = saved_etu(saved_events(session, state.get("start_ts")), state.get("req_ts", []),
                      state.get("compact_ts", []), [w[2] if state.get("one_hour") else w[1], w[3]])
    spent = total_etu(state)
    share = saved / (spent + saved) if saved else 0.0
    return (f"{head} · {fmt_num(spent)} ETU · saved ~{fmt_num(saved)} ETU ({share:.1%}) · "
            + ("/clear or /compact now" if pct >= 1 else f"/clear at {fmt_ctx(cfg['statusline_warn'] * budget)}"))


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
        state = scan(path, state, Pricer(cfg))
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
        if os.environ.get("TOKNIX_DEBUG") == "1":
            import traceback
            traceback.print_exc(file=sys.stderr)
    return 0
