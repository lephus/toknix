"""Output filters: keep what an agent needs to act, drop what it would only skim.

Every filter is a pure function  lines -> lines  so it is easy to test against real fixtures.
Rules shared by all filters:
  * error / failure / warning lines are never dropped by a specific filter;
  * the final summary line of a tool is always kept;
  * when in doubt, keep the line (the budget step in runner.py trims later, and the full
    output stays recallable with `tokopt recall <id>`).
"""
from __future__ import annotations

import re
import shlex

ERROR_RE = re.compile(
    r"(?i)\b(error|errors|failed|failure|fail|fatal|exception|traceback|panic|assert(ion)?|"
    r"cannot|could not|not found|undefined|denied|refused|segfault|warning|warn)\b|^E\s|^\s*at\s.+:\d+"
)

WRAPPERS = {"sudo", "npx", "pnpx", "bunx", "time", "nice", "env", "command", "exec"}


def program_of(cmd: str) -> tuple[str, list[str]]:
    """Return (program, args) for the first real program in a command line.

    Skips VAR=value prefixes and wrappers like `npx`, `uv run`, `poetry run`, `python -m`.
    """
    first = re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd.strip(), maxsplit=1)[0]
    try:
        words = shlex.split(first)
    except ValueError:
        words = first.split()
    while words:
        w = words[0]
        if "=" in w and not w.startswith(("-", "=")) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            words = words[1:]
        elif w in WRAPPERS:
            words = words[1:]
        elif w in ("uv", "poetry", "pipenv", "pdm", "hatch") and len(words) > 1 and words[1] == "run":
            words = words[2:]
        elif re.match(r"^python[0-9.]*$", w) and len(words) > 2 and words[1] == "-m":
            words = words[2:]
        else:
            break
    if not words:
        return "", []
    prog = words[0].rsplit("/", 1)[-1] if not words[0].startswith("./") else words[0]
    return prog, words[1:]


def _git_sub(args: list[str]) -> str:
    """Subcommand after git's global options (`-C dir`, `-c k=v`, `--no-pager`, `--git-dir=…`)."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
        elif a == "--no-pager" or a.startswith(("--git-dir=", "--work-tree=")):
            i += 1
        else:
            return a
    return ""


def classify(cmd: str) -> str:
    prog, args = program_of(cmd)
    sub = args[0] if args else ""
    if prog in ("pytest", "py.test"):
        return "pytest"
    if prog in ("jest", "vitest", "mocha"):
        return "jest"
    if prog in ("npm", "pnpm", "yarn", "bun"):
        if sub in ("test", "t") or (sub == "run" and len(args) > 1 and "test" in args[1]):
            return "jest"
        if sub in ("install", "i", "ci", "add", "update", "upgrade", "remove", "uninstall") or (prog == "yarn" and not args):
            return "install"
        return "generic"
    if prog == "go" and sub == "test":
        return "gotest"
    if prog == "cargo" and sub in ("test", "nextest"):
        return "cargotest"
    if prog == "cargo" and sub in ("build", "check", "clippy"):
        return "compile"
    if prog in ("pip", "pip3") and sub == "install":
        return "install"
    if prog == "uv" and sub in ("pip", "add", "sync", "lock"):
        return "install"
    if prog in ("poetry", "composer", "bundle") and sub in ("install", "update", "add", "lock"):
        return "install"
    if prog in ("tsc", "eslint", "ruff", "mypy", "flake8", "pylint"):
        return "lint"
    if prog == "git":
        sub = _git_sub(args)
        return {"status": "gitstatus", "log": "gitlog", "diff": "gitdiff", "show": "gitdiff",
                "push": "gitnet", "pull": "gitnet", "fetch": "gitnet", "clone": "gitnet"}.get(sub, "generic")
    if prog in ("ls", "find", "tree", "du"):
        return "listing"
    if prog in ("docker", "mvn", "gradle", "./gradlew", "make", "dotnet", "flutter", "terraform"):
        return "build"
    return "generic"


# --------------------------------------------------------------------------- helpers
def dedupe_runs(lines: list[str]) -> list[str]:
    """Collapse identical consecutive lines into one line with a repeat count."""
    out: list[str] = []
    prev, n = None, 0
    for l in lines + [None]:  # type: ignore[list-item]
        if l == prev:
            n += 1
            continue
        if prev is not None:
            out.append(prev if n == 1 else f"{prev}  [×{n}]")
        prev, n = l, 1
    return out


def squeeze_blank(lines: list[str]) -> list[str]:
    out: list[str] = []
    for l in lines:
        if not l.strip() and (not out or not out[-1].strip()):
            continue
        out.append(l)
    while out and not out[-1].strip():
        out.pop()
    return out


def collapse_similar(lines: list[str], min_run: int = 4) -> list[str]:
    """Collapse runs of lines that differ only in numbers/hashes (e.g. 'Downloading x 12%')."""
    def shape(l: str) -> str:
        return re.sub(r"[0-9a-f]{7,}|\d+(\.\d+)?", "#", l)

    out, i = [], 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and shape(lines[j + 1]) == shape(lines[i]) and lines[i].strip():
            j += 1
        run = j - i + 1
        if run >= min_run and not ERROR_RE.search(lines[i]):
            out += [lines[i], f"  … {run - 2} similar lines …", lines[j]]
        else:
            out += lines[i:j + 1]
        i = j + 1
    return out


# --------------------------------------------------------------------------- specific filters
def f_pytest(lines: list[str], failed: bool) -> list[str]:
    keep, in_detail = [], False
    for l in lines:
        s = l.strip()
        if re.match(r"^=+ (FAILURES|ERRORS|short test summary info|warnings summary) =+$", s):
            in_detail = True
        if in_detail:
            keep.append(l)
            continue
        if s.startswith(("platform ", "cachedir:", "rootdir:", "plugins:", "configfile:", "collecting ...")):
            continue
        if re.search(r"\bPASSED\b|\bSKIPPED\b|\bXFAIL\b", s) and "FAILED" not in s and "ERROR" not in s:
            continue
        if re.match(r"^[\w/.\-]+\.py [.sxX]+\s*(\[\s*\d+%\])?$", s):  # dots-only progress line
            continue
        if re.match(r"^=+ test session starts =+$", s):
            continue
        keep.append(l)
    return keep


def f_jest(lines: list[str], failed: bool) -> list[str]:
    keep = []
    for l in lines:
        s = l.strip()
        if re.match(r"^(✓|√|PASS\b|○|↓)", s) or re.match(r"^✓ ", s):
            continue
        if re.match(r"^\s*(✓|√)\s", l):
            continue
        keep.append(l)
    return keep


def f_gotest(lines: list[str], failed: bool) -> list[str]:
    return [l for l in lines if not re.match(r"^(=== (RUN|PAUSE|CONT)|\s*--- PASS:|PASS$|ok\s)", l)]


def f_cargotest(lines: list[str], failed: bool) -> list[str]:
    return [l for l in lines if not re.match(r"^test .+ \.\.\. ok$|^\s*(Compiling|Downloaded|Downloading|Fresh|Checking) ", l)]


def f_install(lines: list[str], failed: bool) -> list[str]:
    noise = re.compile(
        r"^\s*(Collecting|Downloading|Using cached|Obtaining|Requirement already satisfied|Building wheel|"
        r"Created wheel|Stored in directory|Preparing metadata|Installing build dependencies|Getting requirements|"
        r"Resolved|Prepared|Audited|Fetching|Progress:|Packages:|Downloaded|Compiling)\b"
        r"|^\s*(━|\+{3,})"  # progress bars: `━` and `+` are not word chars, so no \b after them
    )
    deprec = [l for l in lines if re.search(r"(?i)npm warn deprecated|deprecated", l)]
    keep = [l for l in lines if not noise.match(l) and not re.search(r"(?i)warn deprecated", l)]
    if deprec:
        names = [re.sub(r"(?i).*deprecated\s+([^:]+):.*", r"\1", d).strip() for d in deprec]
        keep.insert(0, f"[tokopt] {len(deprec)} deprecation warnings: {', '.join(names[:8])}{' …' if len(names) > 8 else ''}")
    return keep


def f_lint(lines: list[str], failed: bool, per_file: int = 15) -> list[str]:
    """Keep every error diagnostic; cap the other lines of each file; keep the totals line."""
    out, counts, other = [], {}, {}
    for l in lines:
        m = re.match(r"^([^\s:(]+\.[A-Za-z]{1,5})[(:]", l.strip())
        if m:
            f = m.group(1)
            counts[f] = counts.get(f, 0) + 1
            if not ERROR_RE.search(l):
                other[f] = other.get(f, 0) + 1
                if other[f] == per_file + 1:
                    out.append(f"  … more non-error lines in {f} omitted (see total below)")
                if other[f] > per_file:
                    continue
        out.append(l)
    if counts:
        out.append(f"[tokopt] {sum(counts.values())} diagnostics in {len(counts)} files")
    return out


def f_gitstatus(lines: list[str], failed: bool) -> list[str]:
    return [l for l in lines if not re.match(r'^\s*\(use "git ', l)]


def f_gitlog(lines: list[str], failed: bool, max_commits: int = 30) -> list[str]:
    """Default `git log` → one line per commit: hash date author: subject."""
    if not any(l.startswith("commit ") for l in lines[:3]):
        return lines
    out, cur, total = [], {}, 0

    def flush():
        nonlocal total
        if cur:
            total += 1
            if total <= max_commits:
                out.append(f"{cur.get('h', '')[:9]} {cur.get('d', '')} {cur.get('a', '')}: {cur.get('s', '')}")

    for l in lines:
        if l.startswith("commit "):
            flush()
            cur = {"h": l.split()[1]}
        elif l.startswith("Author:"):
            cur["a"] = re.sub(r"\s*<.*>", "", l[7:].strip())
        elif l.startswith("Date:"):
            parts = l[5:].split()
            cur["d"] = " ".join(parts[1:3] + parts[4:5]) if len(parts) >= 5 else l[5:].strip()
        elif l.startswith("    ") and "s" not in cur and l.strip():
            cur["s"] = l.strip()
    flush()
    if total > max_commits:
        out.append(f"[tokopt] {total - max_commits} older commits omitted (git log -n / --oneline to page)")
    return out


def f_gitdiff(lines: list[str], failed: bool, budget: int = 400) -> list[str]:
    """Keep whole hunks up to the budget, then list the remaining files instead of cutting mid-hunk."""
    if len(lines) <= budget:
        return lines
    out, files_left, used = [], [], 0
    cur_file: list[str] = []
    blocks: list[list[str]] = []
    for l in lines:
        if l.startswith("diff --git") and cur_file:
            blocks.append(cur_file)
            cur_file = []
        cur_file.append(l)
    if cur_file:
        blocks.append(cur_file)
    for b in blocks:
        if used + len(b) <= budget:
            out += b
            used += len(b)
        else:
            m = re.match(r"diff --git a/(\S+)", b[0])
            adds = sum(1 for x in b if x.startswith("+") and not x.startswith("+++"))
            dels = sum(1 for x in b if x.startswith("-") and not x.startswith("---"))
            files_left.append(f"{m.group(1) if m else b[0]} (+{adds} -{dels})")
    if files_left:
        out.append(f"[tokopt] {len(files_left)} more files changed; view one with `git diff -- <file>`:")
        out += [f"  {f}" for f in files_left]
    return out


def f_gitnet(lines: list[str], failed: bool) -> list[str]:
    return [l for l in lines if not re.match(
        r"^(remote: (Enumerating|Counting|Compressing|Total)|Receiving objects|Resolving deltas|Unpacking objects|"
        r"Enumerating objects|Counting objects|Compressing objects|Writing objects|Delta compression)", l)]


def f_listing(lines: list[str], failed: bool, cap: int = 80) -> list[str]:
    if len(lines) <= cap:
        return lines
    return lines[:cap] + [f"[tokopt] {len(lines) - cap} more entries; narrow the path or add filters"]


def f_build(lines: list[str], failed: bool) -> list[str]:
    noise = re.compile(r"^(#\d+ (sha256|extracting|\[internal\])|\s*Downloading|\s*Downloaded|\[INFO\] Download|"
                       r"> Task .* (UP-TO-DATE|NO-SOURCE)$|\s*\d+(\.\d+)?%|.*\b(Pulling fs layer|Waiting|Verifying Checksum|Download complete|Pull complete)\b)")
    return [l for l in lines if not noise.match(l)]


FILTERS = {
    "pytest": f_pytest, "jest": f_jest, "gotest": f_gotest, "cargotest": f_cargotest,
    "install": f_install, "lint": f_lint, "compile": f_lint, "gitstatus": f_gitstatus, "gitlog": f_gitlog,
    "gitdiff": f_gitdiff, "gitnet": f_gitnet, "listing": f_listing, "build": f_build,
}


def budget_cut(lines: list[str], max_lines: int, head: int) -> list[str]:
    """All error lines (outside the budget) + head and tail of the rest within max_lines (A2.6)."""
    err = {i for i, l in enumerate(lines) if ERROR_RE.search(l)}
    rest = [i for i in range(len(lines)) if i not in err]
    if len(rest) <= max_lines:
        return lines
    tail = max(max_lines - head - 20, 20)
    while head + tail > max_lines and tail > 5:
        tail -= 1
    while head + tail > max_lines and head > 5:
        head -= 1
    keep = err | set(rest[:head]) | set(rest[-tail:])
    out, last = [], -1
    for i in sorted(keep):
        if i != last + 1:
            out.append(f"  … {i - last - 1} lines omitted …")
        out.append(lines[i])
        last = i
    return out


def apply(cmd: str, lines: list[str], exit_code: int, cfg: dict) -> tuple[list[str], str]:
    kind = classify(cmd)
    failed = exit_code != 0
    out = FILTERS.get(kind, lambda l, f: l)(lines, failed)
    out = squeeze_blank(collapse_similar(dedupe_runs(out)))
    max_lines = cfg["max_lines"] * (2 if failed else 1)
    if kind != "gitdiff":
        out = budget_cut(out, max_lines, cfg["head_lines"])
    return out, kind
