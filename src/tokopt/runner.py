"""`tokopt run -- <command>`: run a command, print filtered output, keep the raw output recallable."""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time

from tokopt import filters, store
from tokopt.config import load, tokopt_home
from tokopt.textutil import normalize

QUIET_ENV = {"NO_COLOR": "1", "FORCE_COLOR": "0", "TERM": "dumb", "PAGER": "cat", "GIT_PAGER": "cat",
             "PIP_PROGRESS_BAR": "off", "npm_config_progress": "false", "npm_config_fund": "false"}


def tee_dir():
    d = tokopt_home() / "tee"
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


def shell_argv(cmd: str) -> list[str]:
    """Agents write bash syntax, so run it with bash (never the user's fish/zsh)."""
    import shutil
    sh = os.environ.get("TOKOPT_SHELL") or shutil.which("bash") or ("bash" if os.name == "nt" else "/bin/sh")
    return [sh, "-c", cmd]


def run(cmd: str, raw: bool = False) -> int:
    cfg = load()
    env = dict(os.environ, **QUIET_ENV)
    try:
        p = subprocess.run(shell_argv(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    except OSError as e:
        print(f"tokopt: cannot start shell: {e}", file=sys.stderr)
        return 127
    text = p.stdout.decode("utf-8", errors="replace")
    if raw or os.environ.get("TOKOPT_RAW") == "1":
        sys.stdout.write(text)
        return p.returncode
    lines = normalize(text)
    small = len(lines) <= cfg["pass_through_lines"] and len(text) <= cfg["pass_through_chars"]
    if small:
        out, kind, lossy, overflow = lines, "passthrough", False, False
    else:
        out, kind, lossy, overflow = filters.apply(cmd, lines, p.returncode, cfg)
    body = "\n".join(out)
    tee_id = ""
    if not small and (lossy or len(body) < len(text) * 0.9):  # a lossy rewrite is recallable even if small
        tee_id = secrets.token_hex(3)
        (tee_dir() / f"{tee_id}.log").write_text(text, encoding="utf-8")
        body += (f"\n[tokopt: {len(lines)} → {len(out)} lines; full output: tokopt recall {tee_id}"
                 f"{'; exit ' + str(p.returncode) if p.returncode else ''}]")
        if secrets.randbelow(50) == 0:
            prune_tee(cfg["tee_keep_days"])
    sys.stdout.write(body + ("\n" if body and not body.endswith("\n") else ""))
    store.record_run(kind, cmd, p.returncode, len(text), len(body), len(lines), len(out), tee_id, overflow)
    return p.returncode


def recall(tee_id: str, grep: str | None = None, lines: str | None = None) -> int:
    f = tee_dir() / f"{tee_id}.log"
    if not f.exists():
        print(f"tokopt: no saved output '{tee_id}' (kept {load()['tee_keep_days']} days)", file=sys.stderr)
        return 1
    content = f.read_text(encoding="utf-8").splitlines()
    if grep:
        import re
        rx = re.compile(grep)
        content = [f"{i + 1}: {l}" for i, l in enumerate(content) if rx.search(l)]
    if lines:
        a, _, b = lines.partition(":")
        content = content[int(a or 1) - 1:int(b) if b else None]
    print("\n".join(content))
    return 0
