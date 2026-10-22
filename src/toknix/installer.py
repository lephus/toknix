"""`toknix install` / `uninstall` / `doctor` for Claude Code (A8).

install adds three hooks and a statusLine to Claude Code's settings.json, keeping everything else;
uninstall removes exactly what install added. What install created (the file, an empty `hooks`
object…) is recorded in ~/.toknix/install.json so uninstall can put back the settings as they were.
The rules block for CLAUDE.md / AGENTS.md / GEMINI.md comes with P3-T5.
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path, PureWindowsPath

from toknix import __version__, runner, store
from toknix.config import claude_dir, codex_dir, gemini_dir, toknix_home

HOOKS = [("PreToolUse", "Bash", "bash"), ("PreToolUse", "Read|Grep|Glob", "read"),
         ("SessionStart", "compact|clear", "session-start"), ("UserPromptSubmit", "", "prompt"),  # "" = no matcher
         ("PreToolUse", "Agent|Task", "agent")]
# Also matches a quoted path (`'/my dir/toknix' hook bash`) and `python -m toknix hook bash`.
# `tokopt` is the name toknix was published under for its first day: its entries are taken over too.
HOOK_RE = re.compile(r"(?:toknix|tokopt)(?:\.exe)?['\"]?\s+hook\s+(bash|read|session-start|prompt|agent)\b")
STATUS_RE = re.compile(r"(?:toknix|tokopt)(?:\.exe)?['\"]?\s+statusline\b")


class InstallError(Exception):
    pass


def settings_path():
    return claude_dir() / "settings.json"


def toknix_command(argv0: str | None = None, which=None, python: str | None = None) -> str:
    """How Claude Code should start toknix: this console script, else `toknix` on PATH, else `python -m toknix`."""
    argv0 = sys.argv[0] if argv0 is None else argv0
    if re.split(r"[/\\]", argv0)[-1].lower() in ("toknix", "toknix.exe"):
        absolute = os.path.isabs(argv0) or argv0.startswith(("/", "\\")) or PureWindowsPath(argv0).is_absolute()
        path = argv0 if absolute else os.path.abspath(argv0)
        if not PureWindowsPath(path).suffix and os.path.exists(path + ".exe"):
            path += ".exe"  # Windows console scripts get argv[0] without it
        return _quote(path)
    found = (which or shutil.which)("toknix")  # looked up at call time, so tests can patch shutil.which
    return _quote(found) if found else f"{_quote(python or sys.executable)} -m toknix"


def _quote(path: str) -> str:
    """A Windows path becomes C:/… (double-quoted if it has spaces), valid in Git Bash and cmd alike."""
    if PureWindowsPath(path).drive:
        path = path.replace("\\", "/")
        return f'"{path}"' if " " in path else path
    return shlex.quote(path)


# --------------------------------------------------------------------------- files
def _load(path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise InstallError(f"{path} is not valid JSON ({e}); fix it, then run again") from None
    if not isinstance(data, dict):
        raise InstallError(f"{path} is not a JSON object; fix it, then run again")
    return data


def _save(path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".toknix-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _backup(path):
    if path.exists():
        d = toknix_home() / "backups" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, d / path.name)


def _records() -> dict:
    try:
        data = json.loads((toknix_home() / "install.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_records(records: dict):
    f = toknix_home() / "install.json"
    if records:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    elif f.exists():
        f.unlink()


# --------------------------------------------------------------------------- settings.json
def _groups(settings: dict, event: str) -> list:
    hooks = settings.get("hooks")
    groups = hooks.get(event) if isinstance(hooks, dict) else None
    return groups if isinstance(groups, list) else []


def _find_hook(groups: list, name: str) -> dict | None:
    for g in groups:
        for h in (g.get("hooks") if isinstance(g, dict) and isinstance(g.get("hooks"), list) else []):
            m = HOOK_RE.search(str(h.get("command", ""))) if isinstance(h, dict) else None
            if m and m.group(1) == name:
                return h
    return None


def _is_ours(status) -> bool:
    return isinstance(status, dict) and bool(STATUS_RE.search(str(status.get("command", ""))))


def install(exe: str | None = None) -> list[str]:
    msgs = []
    if not exe:
        exe = toknix_command()
        if exe.endswith(" -m toknix"):
            msgs.append(f"note: toknix is not on PATH, so hooks run `{exe}`; "
                        f"check that it works outside this shell with `toknix doctor`")
    path = settings_path()
    current = _load(path)
    s = copy.deepcopy(current) if current is not None else {}
    records = _records()
    created = list(records.get(str(path), {}).get("created", []))
    if current is None:
        created.append("file")
    if "hooks" not in s:
        s["hooks"] = {}
        created.append("hooks")
    elif not isinstance(s["hooks"], dict):
        raise InstallError(f'"hooks" in {path} is not an object; fix it, then run again')
    for event, matcher, name in HOOKS:
        if not isinstance(s["hooks"].get(event), list):
            if event in s["hooks"]:
                raise InstallError(f'"hooks.{event}" in {path} is not a list; fix it, then run again')
            s["hooks"][event] = []
            created.append(f"hooks.{event}")
        command = f"{exe} hook {name}"
        entry = _find_hook(s["hooks"][event], name)
        if entry is None:
            group = {"matcher": matcher} if matcher else {}
            s["hooks"][event].append(dict(group, hooks=[{"type": "command", "command": command}]))
            msgs.append(f"added {event} [{matcher}] → {command}")
        elif entry["command"] != command:
            msgs.append(f"updated {event} [{matcher}]: {entry['command']} → {command}")
            entry["command"] = command
        else:
            msgs.append(f"already installed: {event} [{matcher}] → {command}")
    status = s.get("statusLine")
    if status is None:
        s["statusLine"] = {"type": "command", "command": f"{exe} statusline"}
        msgs.append(f"added statusLine → {exe} statusline")
    elif _is_ours(status):
        status["command"] = f"{exe} statusline"
    else:
        msgs.append("statusLine kept: you already have one (toknix statusline is optional)")
    if s != current:
        _backup(path)
        _save(path, s)
    records[str(path)] = {"created": sorted(set(created)), "command": exe}
    msgs += _install_rules(records)
    _save_records(records)
    msgs.append(f"settings: {path}")
    return msgs


def uninstall() -> list[str]:
    records = _records()
    msgs = _uninstall_settings(records) + _uninstall_rules(records)
    _save_records(records)
    return msgs


def _uninstall_settings(records: dict) -> list[str]:
    path = settings_path()
    current = _load(path)
    record = records.pop(str(path), None)
    created = record["created"] if record else None  # None: no record, drop containers that we emptied

    def may_drop(key: str) -> bool:
        return created is None or key in created

    if current is None:
        return [f"nothing to remove: {path} does not exist"]
    s = copy.deepcopy(current)
    removed = 0
    hooks = s.get("hooks")
    if isinstance(hooks, dict):
        for event in list(hooks):
            groups = hooks[event]
            if not isinstance(groups, list):
                continue
            before = removed
            for g in list(groups):
                if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                    continue
                keep = [h for h in g["hooks"] if not (isinstance(h, dict) and HOOK_RE.search(str(h.get("command", ""))))]
                removed += len(g["hooks"]) - len(keep)
                if not keep and len(keep) != len(g["hooks"]):
                    groups.remove(g)
                else:
                    g["hooks"] = keep
            if not groups and may_drop(f"hooks.{event}") and (record or removed > before):
                del hooks[event]
        if not hooks and may_drop("hooks") and (record or removed):
            del s["hooks"]
    if _is_ours(s.get("statusLine")):
        del s["statusLine"]
        removed += 1
    if s == current:
        return [f"nothing to remove from {path}"]
    _backup(path)
    if s == {} and created and "file" in created:
        path.unlink()
        return [f"removed {removed} toknix entries; deleted {path} (toknix had created it)"]
    _save(path, s)
    return [f"removed {removed} toknix entries from {path}"]


# --------------------------------------------------------------------------- rules block (A8 step 3, P3-T5)
RULES_VERSION = 1
TEMPLATES = Path(__file__).resolve().parent / "templates"
RULES_BLOCK_RE = re.compile(r"<!-- (?:toknix|tokopt):begin v(\d+) -->\r?\n.*?<!-- (?:toknix|tokopt):end -->(?:\r?\n)?",
                            re.S)


def rules_targets() -> list[tuple[str, Path, Path | None]]:
    """(agent, instruction file, directory that must exist first). Claude Code's file is always written;
    Codex and Gemini CLI only when they are installed, so no directory is created for an agent not in use."""
    return [("Claude Code", claude_dir() / "CLAUDE.md", None),
            ("Codex", codex_dir() / "AGENTS.md", codex_dir()),
            ("Gemini CLI", gemini_dir() / "GEMINI.md", gemini_dir())]


def _rules_block(newline: str) -> str:
    rules = (TEMPLATES / "rules.md").read_text(encoding="utf-8")
    return (f"<!-- toknix:begin v{RULES_VERSION} -->{newline}" + rules.replace("\n", newline)
            + f"<!-- toknix:end -->{newline}")


def _read_instructions(path: Path) -> str | None:
    """Text as stored (CRLF kept); None when missing. Not UTF-8 → left alone (InstallError)."""
    if not path.exists():
        return None
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise InstallError(f"{path} is not UTF-8; toknix leaves it alone") from None


def _install_rules(records: dict) -> list[str]:
    msgs = []
    for agent, path, needs in rules_targets():
        if needs is not None and not needs.is_dir():
            continue
        current = _read_instructions(path)
        text = current or ""
        newline = "\r\n" if "\r\n" in text else "\n"
        block = _rules_block(newline)
        m = RULES_BLOCK_RE.search(text)
        if m:  # replace the block wherever it is; the user's text around it stays
            new = text[:m.start()] + block + text[m.end():]
            if new == text:
                msgs.append(f"rules v{RULES_VERSION} already in {path}")
                continue
            msgs.append(f"updated rules v{m.group(1)} → v{RULES_VERSION} in {path}")
        else:  # append after a blank line; remember what was added so uninstall gives back the exact bytes
            sep = "" if not text else newline if text.endswith("\n") else newline * 2
            new = text + sep + block
            records.setdefault(f"rules:{path}", {"created": current is None, "sep": sep})
            msgs.append(f"added rules v{RULES_VERSION} → {path}")
        _backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new.encode("utf-8"))
    return msgs


def _uninstall_rules(records: dict) -> list[str]:
    msgs = []
    for agent, path, _ in rules_targets():
        record = records.pop(f"rules:{path}", None)
        text = _read_instructions(path) if path.exists() else None
        m = RULES_BLOCK_RE.search(text) if text else None
        if not m:
            continue
        before, after = text[:m.start()], text[m.end():]
        if record:
            sep = record["sep"]
            if sep and before.endswith(sep):
                before = before[:-len(sep)]
        else:  # no record: drop the blank line an install would have put before the block
            for blank in ("\r\n", "\n"):
                if before.endswith(blank * 2):
                    before = before[:-len(blank)]
                    break
        new = before + after
        _backup(path)
        if record and record["created"] and not new:
            path.unlink()
            msgs.append(f"removed rules; deleted {path} (toknix had created it)")
        else:
            path.write_bytes(new.encode("utf-8"))
            msgs.append(f"removed rules from {path}")
    return msgs


# --------------------------------------------------------------------------- init-project (A8)
def init_project(root) -> list[str]:
    """Deny Read of the junk directories (Claude Code applies Read rules to Grep and Glob too) in the
    project's .claude/settings.json, and list them in .geminiignore. Existing entries are kept; idempotent."""
    from toknix.config import load
    root = Path(root)
    dirs = [p.strip("/") for p in load()["junk_patterns"] if p.startswith("/") and p.endswith("/")]
    msgs = []
    path = root / ".claude" / "settings.json"
    settings = _load(path) or {}
    perms = settings.setdefault("permissions", {})
    if not isinstance(perms, dict) or not isinstance(perms.setdefault("deny", []), list):
        raise InstallError(f'"permissions.deny" in {path} is not a list; fix it, then run again')
    new = [f"Read(./**/{d}/**)" for d in dirs if f"Read(./**/{d}/**)" not in perms["deny"]]
    if new:
        perms["deny"] += new
        _backup(path)
        _save(path, settings)
        msgs.append(f"added {len(new)} deny rules to {path.relative_to(root).as_posix()}")
    else:
        msgs.append(f"deny rules already in {path.relative_to(root).as_posix()}")
    ignore = root / ".geminiignore"
    text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    have = {line.strip() for line in text.splitlines()}
    missing = [f"{d}/" for d in dirs if f"{d}/" not in have]
    if missing:
        text += ("" if not text or text.endswith("\n") else "\n") + "".join(f"{m}\n" for m in missing)
        ignore.write_text(text, encoding="utf-8")
        msgs.append(f"added {len(missing)} entries to .geminiignore")
    else:
        msgs.append(".geminiignore already lists them")
    return msgs


# --------------------------------------------------------------------------- doctor
def _program(command: str) -> str:
    """The program of a hook command: what comes before `[-m toknix] hook|statusline`, unquoted. Not shlex:
    in POSIX mode it would eat the backslashes of a Windows path."""
    prefix = re.split(r"\s+(?:-m\s+(?:toknix|tokopt)\s+)?(?:hook|statusline)\b", command, maxsplit=1)[0].strip()
    if len(prefix) >= 2 and prefix[0] == prefix[-1] and prefix[0] in "'\"":
        prefix = prefix[1:-1]
    return prefix


def _missing_program(command: str) -> str | None:
    program = _program(command)
    if not program:
        return command
    if "/" in program or "\\" in program:
        return None if os.path.exists(program) or os.path.exists(program + ".exe") else program
    return None if shutil.which(program) else program


def _does_not_run(command: str) -> str | None:
    """Why `<toknix part of the hook command> --version`, run by bash as Claude Code runs hooks, fails (None: it works)."""
    probe = command.rsplit(" hook ", 1)[0] + " --version"
    try:
        p = subprocess.run(runner.shell_argv(probe), capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    out = p.stdout.decode("utf-8", errors="replace").strip()
    if p.returncode == 0 and out.startswith("toknix "):
        return None
    err = p.stderr.decode("utf-8", errors="replace").strip().splitlines()
    return err[-1] if err else out or f"exit {p.returncode}"


def doctor() -> tuple[list[str], bool]:
    path = settings_path()
    lines = [f"toknix {__version__}", f"  command:  {toknix_command()}",
             f"  Python {sys.version.split()[0]} ({sys.executable})", f"  settings: {path}"]
    ok = True
    try:
        settings = _load(path) or {}
    except InstallError as e:
        lines.append(f"  ⚠ {e}")
        settings, ok = {}, False
    probed: dict = {}  # toknix command → why it fails, checked once
    for event, matcher, name in HOOKS:
        entry = _find_hook(_groups(settings, event), name)
        if entry is None:
            lines.append(f"  hook {name:<14} missing ({event} [{matcher}])")
            ok = False
            continue
        missing = _missing_program(entry["command"])
        if missing:
            problem = f"  ⚠ {missing} not found"
        else:
            prefix = entry["command"].rsplit(" hook ", 1)[0]
            if prefix not in probed:
                probed[prefix] = _does_not_run(entry["command"])
            problem = f"  ⚠ does not run: {probed[prefix]}" if probed[prefix] else ""
        lines.append(f"  hook {name:<14} installed: {entry['command']}{problem}")
        ok = ok and not problem
    status = settings.get("statusLine")
    lines.append(f"  statusLine          {'toknix' if _is_ours(status) else 'yours (kept)' if status else 'none'}")
    for agent, rules_path, needs in rules_targets():
        if needs is not None and not needs.is_dir():
            lines.append(f"  {'rules':<19} {agent} not installed (no {needs})")
            continue
        try:
            m = RULES_BLOCK_RE.search(_read_instructions(rules_path) or "")
        except InstallError as e:
            lines.append(f"  {'rules':<19} ⚠ {e}")
            ok = False
            continue
        if m:
            old = "" if int(m.group(1)) == RULES_VERSION else f" (current is v{RULES_VERSION}: run `toknix install`)"
            lines.append(f"  {'rules':<19} v{m.group(1)} in {rules_path}{old}")
        else:
            lines.append(f"  {'rules':<19} missing in {rules_path}")
            ok = False
    runs = guard = 0
    if (toknix_home() / "toknix.db").exists():
        since = time.time() - 7 * 86400
        try:
            with store.db() as c:
                runs = c.execute("SELECT COUNT(*) FROM runs WHERE ts >= ?", (since,)).fetchone()[0]
                guard = c.execute("SELECT COUNT(*) FROM guard WHERE ts >= ?", (since,)).fetchone()[0]
        except sqlite3.Error as e:
            lines.append(f"  ⚠ cannot read {toknix_home() / 'toknix.db'}: {e}")
    lines.append(f"  last 7 days: {runs} runs, {guard} guard events")
    if not ok:
        lines.append("  fix: run `toknix install`, then restart Claude Code")
    return lines, ok
