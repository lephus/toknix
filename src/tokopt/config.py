"""Paths and user configuration (~/.tokopt/config.json overrides DEFAULTS)."""
from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path.home()


def tokopt_home() -> Path:
    return Path(os.environ.get("TOKOPT_HOME", HOME / ".tokopt"))


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", HOME / ".claude"))


def codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME", HOME / ".codex"))


def gemini_dir() -> Path:
    return HOME / ".gemini"


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
    # Read guard
    "guard_junk": True,
    "guard_big_file_lines": 800,  # Read without offset/limit on a larger file is denied with a hint
    "guard_repeat_reads": 2,      # identical unchanged reads allowed per session before denial (0 = off)
    "junk_patterns": [
        "/node_modules/", "/vendor/", "/dist/", "/build/", "/.next/", "/target/", "/coverage/",
        "/.git/", "/__pycache__/", ".min.js", ".min.css", ".map", "package-lock.json",
        "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "composer.lock", "go.sum",
    ],
    # statusline / measurement
    "context_window": 200000,
    "weights": [1.0, 1.25, 0.1, 5.0],  # input, cache_write, cache_read, output → ETU
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    f = tokopt_home() / "config.json"
    if f.exists():
        try:
            cfg.update(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return cfg
