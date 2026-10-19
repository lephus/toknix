"""Small text helpers shared by every module."""
from __future__ import annotations

import math
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


# Runs that may be hex/base64, and runs of non-ASCII characters (A1).
TOKEN_CLASS_RE = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}|[^\x00-\x7f]+")
HEX_RE = re.compile(r"[0-9a-fA-F]+")
DEFAULT_RATIOS = {"default": 4.0, "hex": 2.0, "non_ascii": 1.5}


def _is_hexlike(run: str) -> bool:
    """Hex, or base64 with upper, lower and digits (so long words and paths stay default)."""
    if HEX_RE.fullmatch(run):
        return True
    return (any(c.isupper() for c in run) and any(c.islower() for c in run)
            and any(c.isdigit() for c in run))


def char_classes(text: str) -> dict:
    """Characters per class (A1), in one regex pass."""
    hex_chars = non_ascii = 0
    for m in TOKEN_CLASS_RE.finditer(text):
        run = m.group()
        if ord(run[0]) > 0x7F:
            non_ascii += len(run)
        elif _is_hexlike(run.rstrip("=")):
            hex_chars += len(run)
    return {"default": len(text) - hex_chars - non_ascii, "hex": hex_chars, "non_ascii": non_ascii}


def est_tokens(text: str, ratios: dict | None = None) -> int:
    """Σ ceil(chars_class / ratio_class) over the hex, non_ascii and default classes (A1)."""
    r = DEFAULT_RATIOS if not ratios else {**DEFAULT_RATIOS, **ratios}
    return sum(math.ceil(n / r[k]) for k, n in char_classes(text).items() if n)


def fmt_num(n: float) -> str:
    n = float(n)
    for unit, div in (("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"
