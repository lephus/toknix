"""Claude Code hooks. Each reads the hook JSON on stdin and prints a hook JSON reply (or nothing).

  toknix hook bash           PreToolUse(Bash)            wrap noisy commands in `toknix run --`
  toknix hook read           PreToolUse(Read|Grep|Glob)  block junk paths, whole-file reads of huge
                                                         files, and repeated reads of unchanged files
  toknix hook session-start  SessionStart(compact|clear) forget the read ledger (context was reset)
  toknix hook prompt         UserPromptSubmit            tell the user when the context is over its budget (A7)
  toknix hook agent          PreToolUse(Agent|Task)      give a subagent a cheaper model by its task (A9)

A hook must never break the agent: any internal error results in "no opinion" (exit 0, no output).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from toknix import filters, store
from toknix.config import DEFAULTS, claude_dir, is_junk, load
from toknix.textutil import est_tokens

# A9: what a subagent is asked to do, from its description, then the start of its prompt. The first that matches wins.
ROUTE_KEEP = re.compile(r"\b(?:design\w*|architect\w*|plan|plans|planning|brainstorm\w*|debug\w*|root[- ]cause|"
                        r"diagnos\w*|security|threat\w*|thiết kế|kế hoạch|kiến trúc|gỡ lỗi|nguyên nhân)\b", re.I)
ROUTE_IMPLEMENT = re.compile(r"\b(?:implement\w*|task\s*#?\d+|fix|fixes|fixed|fixing|refactor\w*|"
                             r"write (?:the |a |an )?(?:code|tests?|function)|add (?:a |the )?tests?|"
                             r"viết code|viết test|sửa|triển khai)\b", re.I)
ROUTE_READ = re.compile(r"\b(?:search\w*|find|finds|finding|locate|look up|research\w*|explor\w*|read|reads|reading|"
                        r"review\w*|summar\w*|list|count|audit\w*|inspect\w*|check\w*|"
                        r"tìm\w*|đọc|xem|rà soát|tóm tắt)\b", re.I)
ROUTE_PROMPT_CHARS = 600

# A command that changes the agent's own shell (cwd/env), at the start of any statement: after ; & | or a newline,
# with or without arguments (`npm test; popd`).
STATEFUL = re.compile(r"(?:^|[;&|\n])\s*(?:cd|pushd|popd|export|unset|source|\.|alias|set|ulimit|exec)(?=\s|$|[;&|])")
# One leading `cd <dir> &&` (P7-T3): the cd stays in the agent's shell, only what follows is wrapped.
CD_PREFIX = re.compile(r"""^\s*cd\s+(?:"[^"]*"|'[^']*'|[^\s;&|'"]+)\s*&&\s*""")


def _emit(obj: dict | None):
    if obj:
        sys.stdout.write(json.dumps(obj))


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny", "permissionDecisionReason": reason}}


def should_wrap(cmd: str, tool_input: dict, cfg: dict) -> bool:
    c = cmd.strip()
    if not c or c.startswith(("toknix ", "tokopt ", "TOKNIX_RAW=", "TOKOPT_RAW=")) or re.search(r"tok(?:nix|opt) run", c):
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
    if not isinstance(cmd, str):
        return None
    if should_wrap(cmd, ti, cfg):
        new = dict(ti, command=f"toknix run -- {shlex.quote(cmd)}")
    else:
        m = CD_PREFIX.match(cmd)
        rest = cmd[m.end():] if m else ""
        if not rest or not should_wrap(rest, ti, cfg):
            return None
        new = dict(ti, command=f"{m.group(0)}toknix run -- {shlex.quote(rest)}")
    out = {"hookEventName": "PreToolUse", "updatedInput": new}
    if cfg.get("hook_permission") in ("allow", "ask"):
        out["permissionDecision"] = cfg["hook_permission"]
    return {"hookSpecificOutput": out}


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
        if path and cfg["guard_junk"] and (pat := is_junk(path.rstrip("/") + "/", cfg["junk_patterns"])):
            store.record_guard(session, "junk", path, 0)
            return _deny(f"toknix: '{path}' is a generated/vendor path ({pat}). Search source directories instead, "
                         f"or set TOKNIX guard_junk=false in ~/.toknix/config.json if you really need it.")
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
    if cfg["guard_junk"] and not has_span and (pat := is_junk(path, cfg["junk_patterns"])):  # a slice is allowed (A4 R1)
        size = p.stat().st_size if p.exists() else 0
        store.record_guard(session, "junk", path, size // 4)
        return _deny(f"toknix: '{path}' matches '{pat}' (generated, vendored or lockfile; ~{size // 4:,} tokens). "
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
            return _deny(f"toknix: {path} has {n:,} lines (~{st.st_size // 4:,} tokens). "
                         f"Run `toknix outline {shlex.quote(path)}` to get its symbols with line ranges, "
                         f"then Read only the range you need with offset/limit.")
    limit = cfg["guard_repeat_reads"]
    if limit and session:
        span = f"{ti.get('offset')}:{ti.get('limit')}"
        n = store.bump_read(session, str(p.resolve()), st.st_mtime, span)
        if n > limit:
            est = st.st_size // 4 if not has_span else est_tokens("x" * 40 * int(ti.get("limit") or 200))
            store.record_guard(session, "reread", path, est)
            return _deny(f"toknix: you already read this exact range of {path} {n - 1} times this session and the file "
                         f"has not changed since, so its content is still in your context. Use it, or read a different range.")
    return None


def hook_session_start(data: dict) -> dict | None:
    if data.get("source") in ("compact", "clear") and data.get("session_id"):
        store.reset_reads(data["session_id"])
    return None


def hook_prompt(data: dict) -> dict | None:
    prompt, path = data.get("prompt"), data.get("transcript_path")
    if isinstance(prompt, str) and prompt.lstrip().startswith(("/clear", "/compact")):
        return None
    if not isinstance(path, str):
        return None
    from toknix import pricing, statusline
    last = statusline.last_request(Path(path).expanduser())
    cfg = load()
    budget = cfg["context_budget"]
    if last is None or last[0] < budget:
        return None
    ctx, model, speed, geo = last
    pr = pricing.Pricer(cfg)  # P8-T2: re-read at the price of the last main request's model
    each = ctx * pr.weights(model, ctx, speed, geo)[3]
    rates = pr.rates(model, ctx, speed, geo)
    usd = f", {pricing.fmt_usd(ctx * rates[3] / 1e6)}" if rates else ""
    return {"systemMessage": f"toknix: context {statusline.fmt_ctx(ctx)} is over the {statusline.fmt_ctx(budget)} "
                             f"budget; every request re-reads it (~{statusline.fmt_num(each)} ETU{usd} each). "
                             "Start the next task with /clear, or /compact."}


def classify_task(agent_type: str, description, prompt) -> str | None:
    """A9: "explore" | "implement" | "read", or None to keep the model."""
    if agent_type == "Explore":
        return "explore"
    if agent_type != "general-purpose":
        return None
    for t in (description, prompt[:ROUTE_PROMPT_CHARS] if isinstance(prompt, str) else None):
        if not isinstance(t, str):
            continue
        for rx, kind in ((ROUTE_KEEP, None), (ROUTE_IMPLEMENT, "implement"), (ROUTE_READ, "read")):
            if rx.search(t):
                return kind
    return None


def main_model(path: Path, tail: int = 1 << 20) -> str:
    """A9: the main session's model: the last `api-request` line (written before the request, so it is there on the
    first response too, and follows /model), else the last main-chain assistant line; "" when neither is found."""
    with open(path, "rb") as fh:
        fh.seek(max(0, fh.seek(0, os.SEEK_END) - tail))
        data = fh.read()
    for raw in reversed(data.split(b"\n")):
        if b'"api-request"' not in raw and b'"assistant"' not in raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "api-request":
            model = (obj.get("params") or {}).get("model") if isinstance(obj.get("params"), dict) else None
        elif obj.get("type") == "assistant" and obj.get("isSidechain") is not True and isinstance(obj.get("message"), dict):
            model = obj["message"].get("model")
        else:
            continue
        if isinstance(model, str) and model and not model.startswith("<"):  # skip "<synthetic>" messages
            return model
    return ""


def hook_agent(data: dict) -> dict | None:
    cfg = load()
    ti = data.get("tool_input")
    if not cfg.get("route_subagents") or data.get("tool_name") not in ("Agent", "Task") or not isinstance(ti, dict):
        return None
    if isinstance(ti.get("model"), str) and ti["model"]:
        return None  # the agent or the user chose the model
    agent_type = ti.get("subagent_type") or "general-purpose"
    if not isinstance(agent_type, str) or agent_type not in cfg["route_agent_types"]:
        return None
    dirs = [claude_dir() / "agents"] + ([Path(data["cwd"]) / ".claude" / "agents"] if data.get("cwd") else [])
    if any((d / f"{agent_type}.md").is_file() for d in dirs):
        return None  # the user's definition of the agent decides its model
    kind = classify_task(agent_type, ti.get("description"), ti.get("prompt"))
    target = dict(DEFAULTS["route_models"], **cfg["route_models"]).get(kind) if kind else None
    path = data.get("transcript_path")
    if not isinstance(target, str) or not target or not isinstance(path, str):
        return None
    try:
        main = main_model(Path(path).expanduser())
    except OSError:
        return None
    from toknix import pricing
    pr = pricing.Pricer(cfg)
    main_rates = pr.rates(main) if main else None
    to_rates = pr.rates(dict(DEFAULTS["model_aliases"], **cfg["model_aliases"]).get(target, target))
    if not main_rates or not to_rates or to_rates[0] >= main_rates[0]:
        return None  # only ever route down
    store.record_route(data.get("session_id", ""), agent_type, kind, main, target)
    out = {"hookEventName": "PreToolUse", "updatedInput": dict(ti, model=target)}
    if cfg.get("hook_permission") in ("allow", "ask"):
        out["permissionDecision"] = cfg["hook_permission"]
    return {"hookSpecificOutput": out}


HOOKS = {"bash": hook_bash, "read": hook_read, "session-start": hook_session_start, "prompt": hook_prompt,
         "agent": hook_agent}


def _read_stdin() -> str:
    """Claude Code sends UTF-8 JSON; never decode it with the console's code page (cp1252 on Windows)."""
    buf = getattr(sys.stdin, "buffer", None)
    return buf.read().decode("utf-8") if buf is not None else sys.stdin.read()


def main(name: str) -> int:
    try:
        data = json.loads(_read_stdin())
        _emit(HOOKS[name](data))
    except Exception as e:  # never block the agent because of toknix
        if os.environ.get("TOKNIX_DEBUG"):
            print(f"toknix hook {name}: {e!r}", file=sys.stderr)
    return 0
