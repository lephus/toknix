"""`tokopt symbol` and `tokopt refs` (A5.2): find code by name without reading whole files.

Both list the repo's source files once (git ls-files when possible), keep only the files whose
bytes contain the name, and only then parse them, so a large repo stays fast.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

from tokopt import langs, outline
from tokopt.config import is_junk, load
from tokopt.runner import write_out

SOURCE_EXT = set(langs.LANG_BY_EXT) | outline.PYTHON_EXT
MAX_BYTES = 1_000_000
MAX_SHOWN, MAX_SYMBOL_LINES, MAX_REF_LINES = 3, 300, 50
_STRING_PARTS = {tokenize.STRING, tokenize.COMMENT} | {getattr(tokenize, n) for n in (
    "FSTRING_MIDDLE", "TSTRING_MIDDLE") if hasattr(tokenize, n)}


# --------------------------------------------------------------------------- source files
def source_files(root: Path, cfg: dict | None = None) -> list[Path]:
    """Source files under root: git's view (tracked + untracked, not ignored) or a directory walk;
    junk paths, files over 1 MB, binary files and unsupported languages left out."""
    cfg = cfg or load()
    root = Path(root)
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk(root, cfg["junk_patterns"])
    out = []
    for p in candidates:
        if p.suffix.lower() not in SOURCE_EXT or is_junk(p.relative_to(root).as_posix(), cfg["junk_patterns"]):
            continue
        try:
            if not p.is_file() or p.stat().st_size > MAX_BYTES:
                continue
            with open(p, "rb") as f:
                if b"\x00" in f.read(8192):
                    continue
        except OSError:
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())


def _git_files(root: Path) -> list[Path] | None:
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    names = dict.fromkeys(n for n in r.stdout.decode("utf-8", errors="surrogateescape").split("\0") if n)
    return [root / n for n in names]


def _walk(root: Path, patterns: list[str]) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root).as_posix()
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not is_junk(f"{rel}/{d}/", patterns))
        out += [Path(dirpath) / f for f in filenames]
    return out


def display(path: Path) -> str:
    """Relative to the working directory when under it (short), otherwise absolute."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def _text(path: Path) -> str:
    return outline.read_source(path) if path.suffix.lower() in outline.PYTHON_EXT else outline.read_text(path)


def _word(name: str) -> re.Pattern:
    return re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")


def _word_bytes(name: str) -> tuple[bytes, re.Pattern]:
    raw = name.encode("utf-8")
    return raw, re.compile(rb"(?<![\w$])" + re.escape(raw) + rb"(?![\w$])")  # ASCII \w: never stricter


def mentions_word(path: Path, word: tuple[bytes, re.Pattern]) -> bool:
    """Cheap check before masking a file: the name as a whole word anywhere in its bytes."""
    raw, rx = word
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return raw in data and rx.search(data) is not None


PY_DECL = r"^\s*(?:async\s+)?(?:def|class)\s+{}\b"
RUBY_DECL = r"^\s*(?:(?:private|protected|public)\s+)?(?:def\s+(?:self\.)?|class\s+|module\s+)(?:[\w:]*::)?{}(?![\w])"


def might_declare(path: Path, name: str) -> bool:
    """Cheap check before parsing a file: some line holding the name matches a declaration pattern of the
    language with that name captured. A superset of the real declarations (scope is not checked), so it
    only skips files that cannot declare the name, e.g. ones that merely import or call it."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    lang = langs.LANG_BY_EXT.get(path.suffix.lower())
    if name.encode("utf-8") not in data and not (lang == "kotlin" and name == "Companion"):
        return False
    text = data.decode("utf-8", errors="replace")
    if lang == "kotlin" and name == "Companion" and re.search(r"\bcompanion\s+object\b", text):
        return True  # an unnamed companion object is named by extract, not in the source
    if name not in text:
        return False
    if lang is None or lang == "ruby":
        rx = re.compile((PY_DECL if lang is None else RUBY_DECL).format(re.escape(name)), re.M)
        return rx.search(text) is not None
    for line in text.split("\n"):
        if name not in line:
            continue
        head = line[:langs.MAX_DECL_CHARS]
        for rx, _, _ in langs.COMPILED[lang]:
            m = rx.match(head)
            if m and any(g and g.strip("`") in (name, f"{g.split('.')[0]}.{name}") for g in m.groups()):
                return True
    return False


# --------------------------------------------------------------------------- symbol
def qualname(s) -> str:
    return f"{s.parent}.{s.name}" if s.parent else s.name


def find_symbols(query: str, files: list[Path], kind: str | None = None) -> list[tuple[Path, object]]:
    """`name` matches every symbol so named; `Class.method` (any depth) matches the tail of the dotted name."""
    leaf = query.split(".")[-1]
    out = []
    for p in files:
        if not might_declare(p, leaf):
            continue
        try:
            symbols = outline.extract(p) or []
        except (outline.CannotOutline, OSError):
            continue
        for s in symbols:
            if s.name != leaf or (kind and s.kind != kind):
                continue
            q = qualname(s)
            if "." in query and q != query and not q.endswith("." + query):
                continue
            out.append((p, s))
    return out


def symbol(query: str, path: str | None = None, kind: str | None = None) -> int:
    files = source_files(Path(path or "."))
    found = find_symbols(query, files, kind)
    if not found:
        what = f"{kind} " if kind else ""
        print(f"tokopt symbol: no {what}symbol named {query!r} in {len(files)} source files", file=sys.stderr)
        return 1
    out = []
    for p, s in found[:MAX_SHOWN]:
        lines = _text(p).split("\n")
        out.append(f"{display(p)}:{s.start}-{s.end}  {s.kind} {qualname(s)}")
        last = min(s.end, s.start + MAX_SYMBOL_LINES - 1)
        out += [f"{i:>5}  {lines[i - 1]}".rstrip() for i in range(s.start, last + 1)]
        if last < s.end:
            out.append(f"  … {s.end - last} more lines; Read {display(p)} with offset={last + 1} limit={s.end - last}")
    out += [f"also: {display(p)}:{s.start}-{s.end}  {s.kind} {qualname(s)}" for p, s in found[MAX_SHOWN:]]
    n = len(found)
    out.append(f"[{n} match{'es' if n > 1 else ''}" + (f"; code shown for the first {MAX_SHOWN}]" if n > MAX_SHOWN else "]"))
    write_out("\n".join(out) + "\n")
    return 0


# --------------------------------------------------------------------------- refs
def mask_python(text: str) -> str:
    """Strings and comments blanked (code inside f-string braces kept on Python 3.12+)."""
    lines = [list(l) for l in text.split("\n")]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in _STRING_PARTS:
                continue
            (r1, c1), (r2, c2) = tok.start, tok.end
            for r in range(r1, r2 + 1):
                row = lines[r - 1]
                for c in range(c1 if r == r1 else 0, c2 if r == r2 else len(row)):
                    row[c] = " "
    except (tokenize.TokenError, SyntaxError):
        return text
    return "\n".join("".join(l) for l in lines)


def _code_view(path: Path, text: str) -> str:
    lang = langs.LANG_BY_EXT.get(path.suffix.lower())
    return langs.mask(text, lang, keep_interpolations=True)[0] if lang else mask_python(text)


def find_refs(name: str, files: list[Path]) -> tuple[list[tuple[Path, int, str]], list[str]]:
    """(references, definitions): \\bname\\b in code (not in strings or comments), minus the line that
    declares each symbol of that name."""
    leaf = name.split(".")[-1]
    rx, word = _word(leaf), _word_bytes(leaf)
    refs, defs = [], []
    for p in files:
        if not mentions_word(p, word):
            continue
        try:
            text = _text(p)
        except (outline.CannotOutline, OSError):
            continue
        lines, code = text.split("\n"), _code_view(p, text).split("\n")
        declared = set()
        try:
            symbols = (outline.extract(p) or []) if might_declare(p, leaf) else []
        except outline.CannotOutline:
            symbols = []
        for s in symbols:
            if s.name == leaf:  # the declaration: the first line of the symbol that names it
                line = next((i for i in range(s.start, s.end + 1) if rx.search(code[i - 1])), s.start)
                declared.add(line)
                defs.append(f"{display(p)}:{line}")
        refs += [(p, i, lines[i - 1].strip()[:200]) for i, c in enumerate(code, 1) if i not in declared and rx.search(c)]
    return refs, defs


def _ranges(numbers: list[int]) -> str:
    parts, start = [], None
    for k, n in enumerate(numbers):
        if start is None:
            start = n
        if k + 1 == len(numbers) or numbers[k + 1] != n + 1:
            parts.append(str(start) if start == n else f"{start}-{n}")
            start = None
    return ", ".join(parts)


def refs(name: str, path: str | None = None) -> int:
    files = source_files(Path(path or "."))
    found, defs = find_refs(name, files)
    if not found:
        print(f"tokopt refs: no references to {name!r} in {len(files)} source files", file=sys.stderr)
        return 1
    out, hidden, current = [], {}, None
    for k, (p, line, text) in enumerate(found):
        if k >= MAX_REF_LINES:
            hidden.setdefault(display(p), []).append(line)
            continue
        if p != current:
            out.append(display(p))
            current = p
        out.append(f"{line:>6}: {text}")
    out += [f"{f}: {len(lines)} more at lines {_ranges(lines)}" for f, lines in hidden.items()]
    shown = "" if not hidden else f", {MAX_REF_LINES} shown"
    files_with = len({p for p, _, _ in found})
    tail = f"; definition not listed: {', '.join(defs)}" if defs else ""
    out.append(f"[{len(found)} references in {files_with} files{shown}{tail}]")
    write_out("\n".join(out) + "\n")
    return 0
