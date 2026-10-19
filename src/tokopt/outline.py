"""Symbols of a source file with their line ranges (A5.1), and `tokopt outline` (A5.2).

Python goes through `ast`, so it is exact. Other languages come with P3-T2; until then outline
prints the first lines of the file instead.
"""
from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path
from typing import NamedTuple

from tokopt.config import load
from tokopt.runner import write_out
from tokopt.textutil import est_tokens

PYTHON_EXT = {".py", ".pyi", ".pyw"}
FALLBACK_LINES = 200
OPENERS, CLOSERS = "([{", ")]}"
SKIPPED = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}


class Symbol(NamedTuple):
    name: str
    kind: str           # class | function | method (a def directly inside a class)
    start: int          # 1-based, decorators included
    end: int
    parent: str | None  # dotted name of the enclosing class/def, e.g. "Shop.Item"
    signature: str      # the header on one line, without comments and the final ":"


class CannotOutline(Exception):
    pass


def read_source(path) -> str:
    """Python source as text: coding cookie and BOM honoured, newlines as \\n (the numbering ast uses)."""
    data = Path(path).read_bytes()
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        text = data.decode(encoding)
    except (SyntaxError, LookupError, UnicodeDecodeError) as e:
        raise CannotOutline(f"cannot decode: {e}") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract(path) -> list[Symbol] | None:
    """Symbols in source order; None when the language is not supported."""
    if Path(path).suffix.lower() not in PYTHON_EXT:
        return None
    text = read_source(path)
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise CannotOutline(f"{e.msg} at line {e.lineno}") from None
    except ValueError as e:  # e.g. null bytes
        raise CannotOutline(str(e)) from None
    lines = text.split("\n")
    out: list[Symbol] = []

    def visit(node, parent: str | None, in_class: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                is_class = isinstance(child, ast.ClassDef)
                start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                out.append(Symbol(child.name, "class" if is_class else "method" if in_class else "function",
                                  start, child.end_lineno, parent, _header(lines, child.lineno, child.col_offset)))
                visit(child, f"{parent}.{child.name}" if parent else child.name, is_class)
            else:  # defs inside if/try/with blocks belong to the same parent
                visit(child, parent, in_class)

    visit(tree, None, False)
    return out


def _header(lines: list[str], lineno: int, col: int) -> str:
    """`def name(...) -> T` / `class Name(...)` from the source, on one line: comments dropped, whitespace
    collapsed, a trailing comma before a closing bracket on its own line dropped, strings verbatim."""
    src = iter([lines[lineno - 1][col:] + "\n"] + [l + "\n" for l in lines[lineno:]])
    parts: list[str] = []
    depth, prev = 0, None
    for tok in tokenize.generate_tokens(lambda: next(src, "")):
        if tok.type in SKIPPED:
            continue
        if tok.type == tokenize.OP and tok.string == ":" and depth == 0:
            break
        new_line = prev is not None and tok.start[0] > prev[0]
        gap = new_line or (prev is not None and tok.start[1] > prev[1])
        if tok.type == tokenize.OP and tok.string in CLOSERS:
            depth -= 1
            if new_line and parts and parts[-1] == ",":
                parts.pop()
        elif gap and parts and parts[-1] not in OPENERS:
            parts.append(" ")
        if tok.type == tokenize.OP and tok.string in OPENERS:
            depth += 1
        parts.append(tok.string)
        prev = tok.end
    return "".join(parts)


def _line_count(text: str) -> int:
    return len(text.split("\n")) - (1 if text.endswith("\n") else 0)


def render(path, symbols: list[Symbol], text: str, ratios: dict) -> str:
    footer = f"[{_line_count(text)} lines, ~{est_tokens(text, ratios)} tokens"
    if not symbols:
        return f"no functions or classes\n{footer}]"
    out = [f"{'  ' * (s.parent.count('.') + 1 if s.parent else 0)}L{s.start}-{s.end}  {s.signature}" for s in symbols]
    big = max((s for s in symbols if s.parent is None), key=lambda s: s.end - s.start)  # first on ties
    out.append(f"{footer}; Read with offset={big.start} limit={big.end - big.start + 1}]")
    return "\n".join(out)


def run(file: str) -> int:
    path = Path(file)
    if not path.exists():
        print(f"tokopt outline: no such file: {file}", file=sys.stderr)
        return 1
    if path.is_dir():
        print(f"tokopt outline: {file} is a directory", file=sys.stderr)
        return 1
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        print(f"tokopt outline: {file} looks like a binary file", file=sys.stderr)
        return 1
    try:
        symbols = extract(path)
        reason = None if symbols is not None else f"no outline for {path.suffix or 'extensionless'} files yet"
    except CannotOutline as e:
        symbols, reason = None, f"cannot outline {path.name} ({e})"
    if symbols is None:
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
        lines = text.split("\n")[:_line_count(text)]
        head = lines[:FALLBACK_LINES]
        write_out("\n".join([f"[tokopt: {reason}; first {len(head)} of {len(lines)} lines]"] + head) + "\n")
        return 0
    write_out(render(path, symbols, read_source(path), load().get("token_ratios")) + "\n")
    return 0
