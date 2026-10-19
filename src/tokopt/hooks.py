"""Claude Code hooks. Each reads the hook JSON on stdin and prints a hook JSON reply (or nothing).

  tokopt hook bash           PreToolUse(Bash)            wrap noisy commands in `tokopt run --`
  tokopt hook read           PreToolUse(Read|Grep|Glob)  block junk paths, whole-file reads of huge
                                                         files, and repeated reads of unchanged files
  tokopt hook session-start  SessionStart(compact|clear) forget the read ledger (context was reset)

A hook must never break the agent: any internal error results in "no opinion" (exit 0, no output).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from tokopt import filters, store
from tokopt.config import load
from tokopt.textutil import est_tokens

# A command that changes the agent's own shell (cwd/env), at the start of any statement: after ; & | or a newline,
# with or without arguments (`npm test; popd`).
STATEFUL = re.compile(r"(?:^|[;&|\n])\s*(?:cd|pushd|popd|export|unset|source|\.|alias|set|ulimit|exec)(?=\s|$|[;&|])")


def _emit(obj: dict | None):
    if obj:
        sys.stdout.write(json.dumps(obj))


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny", "permissionDecisionReason": reason}}


def should_wrap(cmd: str, tool_input: dict, cfg: dict) -> bool:
    c = cmd.strip()
    if not c or c.startswith(("tokopt ", "TOKOPT_RAW=")) or "tokopt run" in c:
        return False
    if tool_input.get("run_in_background") or c.endswith("&") or "<<" in c:
        return False
    if STATEFUL.search(c):  # the agent's shell must keep cwd/env changes
        return False
    prog, _ = filters.program_of(c)
    return prog in set(cfg["wrap_programs"])


def hook_bash(data: dict) -> dict | None:
    if data.get("tool_name", "Bash") != "Bash":
        return None
    cfg = load()
    ti = data.get("tool_input") or {}
    cmd = ti.get("command", "")
    if not isinstance(cmd, str) or not should_wrap(cmd, ti, cfg):
        return None
    new = dict(ti, command=f"tokopt run -- {shlex.quote(cmd)}")
    out = {"hookEventName": "PreToolUse", "updatedInput": new}
    if cfg.get("hook_permission") in ("allow", "ask"):
        out["permissionDecision"] = cfg["hook_permission"]
    return {"hookSpecificOutput": out}


def _is_junk(path: str, cfg: dict) -> str | None:
    """`/dir/` patterns match a path segment, `.ext` patterns a suffix, other patterns the whole file name."""
    p = "/" + path.replace("\\", "/").lstrip("/")
    for pat in cfg["junk_patterns"]:
        if pat.startswith("/"):
            hit = pat in p + ("/" if pat.endswith("/") else "")
        elif pat.startswith("."):
            hit = p.endswith(pat)
        else:
            hit = p.endswith("/" + pat)
        if hit:
            return pat
    return None


def _count_lines(path: Path, cap_bytes: int = 50_000_000) -> int:
    n = 0
    with path.open("rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            n += b.count(b"\n")
            if fh.tell() > cap_bytes:
                break
    return n


def hook_read(data: dict) -> dict | None:
    cfg = load()
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    session = data.get("session_id", "")
    path = ti.get("file_path") or ti.get("path") or ""
    if tool in ("Grep", "Glob"):
        if path and cfg["guard_junk"] and (pat := _is_junk(path.rstrip("/") + "/", cfg)):
            store.record_guard(session, "junk", path, 0)
            return _deny(f"tokopt: '{path}' is a generated/vendor path ({pat}). Search source directories instead, "
                         f"or set TOKOPT guard_junk=false in ~/.tokopt/config.json if you really need it.")
        limit = cfg["grep_head_limit"]
        if tool == "Grep" and ti.get("output_mode") == "content" and "head_limit" not in ti and limit:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "updatedInput": dict(ti, head_limit=limit)}}  # N1.4
        return None
    if tool != "Read" or not path:
        return None
    p = Path(path)
    if not p.is_absolute() and data.get("cwd"):  # Claude Code sends absolute paths; be safe with relative ones
        p = Path(data["cwd"]) / p
    has_span = ti.get("offset") is not None or ti.get("limit") is not None
    if cfg["guard_junk"] and not has_span and (pat := _is_junk(path, cfg)):  # a slice is allowed (A4 R1)
        size = p.stat().st_size if p.exists() else 0
        store.record_guard(session, "junk", path, size // 4)
        return _deny(f"tokopt: '{path}' matches '{pat}' (generated, vendored or lockfile; ~{size // 4:,} tokens). "
                     f"Read the source or manifest instead. If it is truly needed, read a slice with offset/limit.")
    if not p.is_file():
        return None
    try:
        st = p.stat()
    except OSError:
        return None
    big = cfg["guard_big_file_lines"]
    if big and not has_span and st.st_size > big * 40:
        n = _count_lines(p)
        if n > big:
            store.record_guard(session, "bigfile", path, st.st_size // 4)
            return _deny(f"tokopt: {path} has {n:,} lines (~{st.st_size // 4:,} tokens). "
                         f"Run `tokopt outline {shlex.quote(path)}` to get its symbols with line ranges, "
                         f"then Read only the range you need with offset/limit.")
    limit = cfg["guard_repeat_reads"]
    if limit and session:
        span = f"{ti.get('offset')}:{ti.get('limit')}"
        n = store.bump_read(session, str(p.resolve()), st.st_mtime, span)
        if n > limit:
            est = st.st_size // 4 if not has_span else est_tokens("x" * 40 * int(ti.get("limit") or 200))
            store.record_guard(session, "reread", path, est)
            return _deny(f"tokopt: you already read this exact range of {path} {n - 1} times this session and the file "
                         f"has not changed since, so its content is still in your context. Use it, or read a different range.")
    return None


def hook_session_start(data: dict) -> dict | None:
    if data.get("source") in ("compact", "clear") and data.get("session_id"):
        store.reset_reads(data["session_id"])
    return None


HOOKS = {"bash": hook_bash, "read": hook_read, "session-start": hook_session_start}


def _read_stdin() -> str:
    """Claude Code sends UTF-8 JSON; never decode it with the console's code page (cp1252 on Windows)."""
    buf = getattr(sys.stdin, "buffer", None)
    return buf.read().decode("utf-8") if buf is not None else sys.stdin.read()


def main(name: str) -> int:
    try:
        data = json.loads(_read_stdin())
        _emit(HOOKS[name](data))
    except Exception as e:  # never block the agent because of tokopt
        if os.environ.get("TOKOPT_DEBUG"):
            print(f"tokopt hook {name}: {e!r}", file=sys.stderr)
    return 0
