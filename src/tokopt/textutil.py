"""Small text helpers shared by every module."""
from __future__ import annotations

import re

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)|\x1b[=>]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def resolve_cr(line: str) -> str:
    """A progress bar rewrites the line with \\r; only the last frame is visible."""
    if "\r" in line:
        parts = [p for p in line.split("\r") if p.strip()]
        return parts[-1] if parts else ""
    return line


def normalize(text: str) -> list[str]:
    text = strip_ansi(text).replace("\r\n", "\n")
    return [resolve_cr(l).rstrip() for l in text.split("\n")]


def est_tokens(text: str) -> int:
    """~4 chars per token for English/code. Good enough for relative savings."""
    return (len(text) + 3) // 4


def fmt_num(n: float) -> str:
    n = float(n)
    for unit, div in (("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"
