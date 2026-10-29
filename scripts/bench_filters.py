"""Measure what `toknix run` cuts on the real outputs in tests/fixtures, and what code by name saves.

    python scripts/bench_filters.py [--markdown]

Runs the same pipeline as `toknix run` (runner.filter_output) with a throwaway TOKNIX_HOME. Tokens are
toknix's own estimate (est_tokens). Exit 1 when a filtered output lost an error line (ERROR_RE).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

# fixture → (command it came from, exit code). See tests/fixtures/README.md.
CASES = {
    "pytest_fail.txt": ("python -m pytest -v", 1),
    "jest_fail.txt": ("npx jest --verbose", 1),
    "gotest_fail.txt": ("go test -v ./...", 1),
    "cargotest_fail.txt": ("cargo test", 1),
    "npm_install.txt": ("npm install express typescript eslint", 0),
    "pip_install.txt": ("pip install requests rich", 0),
    "build_timestamps.txt": ("./gradlew bootRun", 1),
    "build_longlines.txt": ("make build", 2),
    "git_log.txt": ("git log", 0),
}

# file → symbol: a whole Read against outline + the one symbol the agent needed
CODE = [("src/toknix/filters.py", "budget_cut"), ("src/toknix/reports.py", "waste_data"),
        ("src/toknix/hooks.py", "hook_agent")]


def cli(*args: str) -> str:
    """stdout of the real CLI, as the agent would get it."""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    return subprocess.run([sys.executable, "-m", "toknix", *args], cwd=ROOT, env=env, capture_output=True,
                          text=True, check=True).stdout


def measure() -> tuple[list[tuple], list[tuple]]:
    home = tempfile.mkdtemp(prefix="toknix-bench-filters-")
    os.environ["TOKNIX_HOME"] = home
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from toknix import runner
        from toknix.filters import ERROR_RE
        from toknix.textutil import est_tokens
        runs = []
        for name, (cmd, code) in CASES.items():
            raw = (FIXTURES / name).read_bytes()
            out = runner.filter_output(cmd, raw, code)
            text = raw.decode("utf-8", errors="replace")
            errors_in = sum(1 for l in text.splitlines() if ERROR_RE.search(l))
            errors_out = sum(1 for l in out.splitlines() if ERROR_RE.search(l))
            runs.append((cmd, len(text.splitlines()), len(out.splitlines()), est_tokens(text), est_tokens(out),
                         errors_in, errors_out))
        reads = []
        for rel, sym in CODE:
            path = ROOT / rel
            whole = est_tokens(path.read_text(encoding="utf-8"))
            shown = est_tokens(cli("outline", rel)) + est_tokens(cli("symbol", sym, "--path", "src"))
            reads.append((f"{rel} → {sym}", whole, shown))
        return runs, reads
    finally:
        shutil.rmtree(home, ignore_errors=True)


def pct(before: int, after: int) -> str:
    return f"-{(1 - after / before) * 100:.0f}%" if after < before else "0%"


def main() -> int:
    runs, reads = measure()
    md = "--markdown" in sys.argv
    if md:
        print("| Command | Lines | Tokens | Saved | Error lines kept |\n|---|---:|---:|---:|---:|")
    for cmd, l_in, l_out, t_in, t_out, e_in, e_out in runs:
        if md:
            print(f"| `{cmd}` | {l_in:,} → {l_out:,} | {t_in:,} → {t_out:,} | **{pct(t_in, t_out)}** | {e_out}/{e_in} |")
        else:
            print(f"{cmd:<40} {l_in:>5} → {l_out:<4} lines  {t_in:>6} → {t_out:<5} tokens  {pct(t_in, t_out):>5}"
                  f"  errors {e_out}/{e_in}")
    t_in, t_out = sum(r[3] for r in runs), sum(r[4] for r in runs)
    print(f"\n{'Total' if md else 'total'}: {t_in:,} → {t_out:,} tokens ({pct(t_in, t_out)})\n")
    if md:
        print("| Read | Whole file | outline + symbol | Saved |\n|---|---:|---:|---:|")
    for label, whole, shown in reads:
        if md:
            print(f"| `{label}` | {whole:,} | {shown:,} | **{pct(whole, shown)}** |")
        else:
            print(f"{label:<40} whole {whole:>6}  outline+symbol {shown:>5}  {pct(whole, shown):>5}")
    return 0 if all(r[6] >= r[5] for r in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
