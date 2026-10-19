"""`toknix run -- <command>`: run a command, print filtered output, keep the raw output recallable."""
from __future__ import annotations

import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path, PureWindowsPath

from toknix import filters, store
from toknix.config import load, toknix_home
from toknix.textutil import char_classes, normalize

QUIET_ENV = {"NO_COLOR": "1", "FORCE_COLOR": "0", "TERM": "dumb", "PAGER": "cat", "GIT_PAGER": "cat",
             "PIP_PROGRESS_BAR": "off", "npm_config_progress": "false", "npm_config_fund": "false"}
TEE_ID_RE = re.compile(r"^[0-9a-f]{1,32}$")  # ids are secrets.token_hex; anything else never touches the disk
WSL_BASH_RE = re.compile(r"(?i)\\windows\\system32\\|\\windowsapps\\")


def tee_dir() -> Path:
    d = toknix_home() / "tee"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prune_tee(days: int):
    cutoff = time.time() - days * 86400
    for f in tee_dir().glob("*.log"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def find_bash(windows: bool | None = None, env=None, which=shutil.which, exists=os.path.isfile) -> str:
    """Agents write bash, so run it with bash (never the user's fish/zsh). On Windows that is Git Bash,
    like Claude Code (A2.1): never WSL's System32\\bash.exe, which runs the command in another OS."""
    windows = os.name == "nt" if windows is None else windows
    env = os.environ if env is None else env
    if env.get("TOKNIX_SHELL"):
        return env["TOKNIX_SHELL"]
    if not windows:
        return which("bash") or "/bin/sh"
    candidates = [env.get("CLAUDE_CODE_GIT_BASH_PATH", "")]
    git = which("git")
    if git:  # Git\cmd\git.exe, Git\bin\git.exe or Git\mingw64\bin\git.exe → Git\bin\bash.exe
        candidates += [str(d / "bin" / "bash.exe") for d in list(PureWindowsPath(git).parents)[:3]]
    for c in candidates:
        if c and exists(c):
            return c
    bash = which("bash")
    return bash if bash and not WSL_BASH_RE.search(bash) else "bash"


def shell_argv(cmd: str) -> list[str]:
    return [find_bash(), "-c", cmd]


def write_out(data: str | bytes):
    """UTF-8 bytes whatever the console encoding, and no \\r\\n translation on Windows."""
    buf = getattr(sys.stdout, "buffer", None)
    if buf is None:  # stdout replaced by a text stream (tests)
        sys.stdout.write(data if isinstance(data, str) else data.decode("utf-8", errors="replace"))
        return
    sys.stdout.flush()
    buf.write(data.encode("utf-8", errors="replace") if isinstance(data, str) else data)
    buf.flush()


def output_lines(text: str) -> list[str]:
    """normalize() without the empty item after a final newline: footer counts and --lines agree."""
    lines = normalize(text)
    if len(lines) > 1 and lines[-1] == "" and text.endswith("\n"):
        lines.pop()
    return lines


def exit_status(returncode: int) -> int:
    """Killed by signal N → 128 + N, as a shell reports it."""
    return 128 - returncode if returncode < 0 else returncode


def run(cmd: str, raw: bool = False) -> int:
    env = dict(os.environ, **QUIET_ENV)
    try:
        p = subprocess.run(shell_argv(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    except OSError as e:
        print(f"toknix: cannot start shell: {e}", file=sys.stderr)
        return 127
    code = exit_status(p.returncode)
    if raw or os.environ.get("TOKNIX_RAW") == "1":
        write_out(p.stdout)
        return code
    try:
        body = filter_output(cmd, p.stdout, code)
    except Exception:  # a bug in toknix must never cost the agent its output
        if os.environ.get("TOKNIX_DEBUG") == "1":
            traceback.print_exc(file=sys.stderr)
        write_out(p.stdout)
        return code
    write_out(body)
    return code


def filter_output(cmd: str, raw: bytes, code: int) -> str:
    """A2.1 after the command ran: filter, tee when something was cut or rewritten, record the run."""
    cfg = load()
    text = raw.decode("utf-8", errors="replace")
    lines = output_lines(text)
    small = len(lines) <= cfg["pass_through_lines"] and len(text) <= cfg["pass_through_chars"]
    if small:
        out, kind, lossy, overflow = lines, "passthrough", False, False
    else:
        out, kind, lossy, overflow = filters.apply(cmd, lines, code, cfg)
    body = "\n".join(out)
    tee_id = ""
    if not small and (lossy or len(body) < len(text) * 0.9):  # a lossy rewrite is recallable even if small
        tee_id = save_tee(raw, cfg["tee_keep_days"])
        if tee_id:
            body += (f"\n[toknix: {len(lines)} → {len(out)} lines; full output: toknix recall {tee_id};"
                     f" exit {code}]")
    raw_cc, out_cc = char_classes(text), char_classes(body)
    store.record_run(kind, cmd, code, len(text), len(body), len(lines), len(out), tee_id, overflow,
                     {k: raw_cc[k] - out_cc[k] for k in raw_cc})
    return body + ("\n" if body and not body.endswith("\n") else "")


def save_tee(raw: bytes, keep_days: int) -> str:
    """Save the raw bytes; '' when that fails (then no footer promises a recall)."""
    tee_id = secrets.token_hex(3)
    try:
        (tee_dir() / f"{tee_id}.log").write_bytes(raw)
    except OSError:
        return ""
    if secrets.randbelow(50) == 0:
        prune_tee(keep_days)
    return tee_id


def parse_span(spec: str) -> tuple[int, float]:
    """`a:b`, `a:`, `:b` or `a`; 1-based, inclusive."""
    a, sep, b = spec.partition(":")
    try:
        lo = int(a) if a else 1
        hi = (int(b) if b else math.inf) if sep else lo
    except ValueError:
        lo = 0
    if lo < 1:
        raise ValueError(f"--lines expects A:B with line numbers from 1, got {spec!r}")
    return lo, hi


def recall(tee_id: str, grep: str | None = None, lines: str | None = None) -> int:
    """Print a saved output: verbatim bytes, or clean numbered lines with --grep / --lines (A2.7)."""
    try:
        rx = re.compile(grep) if grep else None
        span = parse_span(lines) if lines else None
    except re.error as e:
        print(f"toknix recall: --grep: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"toknix recall: {e}", file=sys.stderr)
        return 2
    f = toknix_home() / "tee" / f"{tee_id}.log"
    if TEE_ID_RE.match(tee_id):
        store.record_recall(tee_id)  # N2.1: how often a cut was not enough
    if not TEE_ID_RE.match(tee_id) or not f.is_file():
        print(f"toknix: no saved output '{tee_id}' (kept {load()['tee_keep_days']} days)", file=sys.stderr)
        return 1
    data = f.read_bytes()
    if rx is None and span is None:
        write_out(data)
        return 0
    text = data.decode("utf-8", errors="replace")
    content = output_lines(text)  # same line numbers as the `N → M lines` footer
    lo, hi = span or (1, math.inf)
    picked = [(i, l) for i, l in enumerate(content, 1) if lo <= i <= hi and (rx is None or rx.search(l))]
    write_out("".join(f"{i}: {l}\n" if rx else f"{l}\n" for i, l in picked))
    return 0
