"""Paths and user configuration (~/.toknix/config.json overrides DEFAULTS)."""
from __future__ import annotations

import json
import os
from pathlib import Path

# Home is read at call time (never at import), so a test or a caller that changes HOME is honoured.
def toknix_home() -> Path:
    return Path(os.environ.get("TOKNIX_HOME") or Path.home() / ".toknix")


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def gemini_dir() -> Path:
    return Path.home() / ".gemini"


DEFAULTS: dict = {
    # output filter
    "max_lines": 120,            # budget for filtered output (doubled when the command fails)
    "max_tokens": 3000,          # token budget for the non-error lines, est_tokens (doubled on failure; A2.6)
    "max_line_chars": 800,       # longer non-error lines keep head 300 + tail 200 chars (A2.5c)
    "normalize_hashes": True,    # install/build/gitnet only (A2.5b)
    "normalize_timestamps": True,
    "normalize_paths": False,    # Claude Code's Read needs absolute paths
    "pass_through_lines": 40,    # outputs at or under this size are never touched
    "pass_through_chars": 4000,
    "head_lines": 30,            # kept from the top when the budget forces a cut
    "tee_keep_days": 7,
    "token_ratios": {"default": 4.0, "hex": 2.0, "non_ascii": 1.5},  # chars per token by class (A1)
    # Bash hook: only these programs are wrapped (first word after env/sudo/npx/uv run...)
    "wrap_programs": [
        "pytest", "py.test", "jest", "vitest", "mocha", "go", "cargo", "npm", "pnpm", "yarn", "bun",
        "pip", "pip3", "uv", "poetry", "tsc", "eslint", "ruff", "mypy", "flake8", "pylint", "git",
        "docker", "make", "mvn", "gradle", "./gradlew", "dotnet", "composer", "bundle", "rspec",
        "flutter", "dart", "terraform", "kubectl", "ls", "find", "tree", "du",
    ],
    "hook_permission": None,      # "allow" / "ask": also send a permissionDecision (A3; "allow" skips the user's prompts)
    # Read guard
    "guard_junk": True,
    "guard_big_file_lines": 800,  # Read without offset/limit on a larger file is denied with a hint
    "guard_repeat_reads": 2,      # identical unchanged reads allowed per session before denial (0 = off)
    "grep_head_limit": 200,       # Grep content mode without head_limit gets this one (0 = off; N1.4)
    "junk_patterns": [
        "/node_modules/", "/vendor/", "/dist/", "/build/", "/.next/", "/target/", "/coverage/",
        "/.git/", "/__pycache__/", ".min.js", ".min.css", ".map", "package-lock.json",
        "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "composer.lock", "go.sum",
    ],
    # toknix map (A5.2): a bigger repo maps only the files changed in the last map_recent_days days
    "map_max_files": 5000,
    "map_recent_days": 90,
    # statusline / measurement
    "context_window": 200000,
    "statusline_warn": 0.6,           # A7: yellow from here, and the "/clear at" hint
    "statusline_alert": 0.8,          # A7: red from here
    "calibration_min_samples": 200,   # A6.4: fewer request pairs → no calibration
    "calibration_max_error": 0.15,    # A6.4: median relative error on held-out pairs must stay below
    "cache_break_min_write": 10000,   # A6.5: a cache break also writes at least this much
    "cache_ttl_seconds": 300,         # A6.5: a gap longer than this is "idle" (3600 once 1-hour writes are seen)
    "gain_transcript_days": 30,       # transcripts read by gain for R̄ and the wrapped share
    "waste_big_output_tokens": 5000,  # A6.2 waste (2)
    "waste_reads_per_session": 3,     # A6.2 waste (4)
    "waste_max_requests": 80,         # A6.2 waste (5)
    "waste_output_share": 0.35,       # A6.2 waste (7) [ASSUMPTION]
    "context_warn_tokens": 1500,      # A6.2 context
    "bench_min_saving": 0.10,         # A6.3 ACCEPT needs B at least this much cheaper (median ETU)…
    "bench_max_pass_drop": 0.0,       # …and its pass rate no lower than A's minus this
    "weights": [1.0, 1.25, 0.1, 5.0],  # input, cache_write, cache_read, output → ETU
    "reread_default": 20,         # R̄ for `gain` until transcripts are read (N1.1)
    "recall_warn_rate": 0.2,      # gain warns when a filter kind is recalled more often than this…
    "recall_warn_min_cuts": 10,   # …over at least this many cut outputs (N2.1)
}


def is_junk(path: str, patterns: list[str]) -> str | None:
    """The junk pattern a path matches: `/dir/` a path segment, `.ext` a suffix, others the whole file name."""
    p = "/" + path.replace("\\", "/").lstrip("/")
    for pat in patterns:
        if pat.startswith("/"):
            hit = pat in p + ("/" if pat.endswith("/") else "")
        elif pat.startswith("."):
            hit = p.endswith(pat)
        else:
            hit = p.endswith("/" + pat)
        if hit:
            return pat
    return None


def load() -> dict:
    cfg = dict(DEFAULTS)
    f = toknix_home() / "config.json"
    if f.exists():
        try:
            cfg.update(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return cfg
