"""Benchmark `tokopt symbol` and `tokopt refs` on a generated git repo (P3-T3: 2,000 files in < 2 s).

    python scripts/bench_codesearch.py [N_FILES]

Wall time of the real CLI, Python start-up included. Exit 1 when a command takes 2 s or more.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 2.0
TARGET = "compute_total"


def _python(i: int, uses: bool) -> str:
    body = "".join(f"def helper_{i}_{k}(items, rate=0.{k + 1}):\n    total = 0\n    for item in items:\n"
                   f"        total += item.price * rate\n    return round(total, 2)\n\n\n" for k in range(12))
    call = f"\ndef report_{i}(items):\n    return {TARGET}(items) + helper_{i}_0(items)\n" if uses else ""
    return f'"""Module {i}."""\nimport math\n\n\n{body}{call}'


def _ts(i: int, uses: bool) -> str:
    methods = "".join(f"  method{k}(x: number): number {{\n    return x * {k} + this.base;\n  }}\n\n" for k in range(14))
    call = f"export function report{i}(items: number[]) {{\n  return {TARGET}(items);\n}}\n" if uses else ""
    return (f"import {{ {TARGET} }} from './lib';\n\nexport class Service{i} {{\n  base = {i};\n\n{methods}}}\n\n{call}"
            if uses else f"export class Service{i} {{\n  base = {i};\n\n{methods}}}\n")


def _go(i: int, uses: bool) -> str:
    funcs = "".join(f"func Handler{i}_{k}(w int, r int) int {{\n\tif w > r {{\n\t\treturn w - r\n\t}}\n"
                    f"\treturn r + {k}\n}}\n\n" for k in range(14))
    call = f"func Report{i}(xs []int) int {{\n\treturn {TARGET}(xs)\n}}\n" if uses else ""
    return f"package pkg{i % 20}\n\n{funcs}{call}"


def _java(i: int, uses: bool) -> str:
    methods = "".join(f"    public int op{k}(int a, int b) {{\n        if (a > b) {{\n            return a - b;\n"
                      f"        }}\n        return a + b + {k};\n    }}\n\n" for k in range(12))
    call = f"    public int report(int[] xs) {{\n        return Lib.{TARGET}(xs);\n    }}\n" if uses else ""
    return f"package com.example.p{i % 20};\n\npublic class Worker{i} {{\n{methods}{call}}}\n"


MAKERS = [(".py", _python), (".ts", _ts), (".go", _go), (".java", _java)]


def make_repo(root: Path, n_files: int) -> None:
    for i in range(n_files):
        ext, make = MAKERS[i % len(MAKERS)]
        d = root / f"mod{i % 50:02d}"
        d.mkdir(exist_ok=True)
        (d / f"file_{i}{ext}").write_text(make(i, uses=i % 40 == 7), encoding="utf-8")
    (root / "lib.py").write_text(f"def {TARGET}(items):\n    return sum(items)\n", encoding="utf-8")
    env = dict(os.environ, GIT_AUTHOR_NAME="b", GIT_AUTHOR_EMAIL="b@b", GIT_COMMITTER_NAME="b",
               GIT_COMMITTER_EMAIL="b@b")
    # No auto gc / maintenance: after a 2,000-file commit git may start one in the background, which then
    # writes into .git/objects while the temp dir is being removed (seen on CI: "Directory not empty").
    no_bg = ["-c", "gc.auto=0", "-c", "maintenance.auto=false"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "bench"]):
        subprocess.run(["git", *no_bg, "-C", str(root), *args], check=True, capture_output=True, env=env)


def run(n_files: int = 2000) -> dict[str, float]:
    """Seconds per command on a fresh repo of n_files files."""
    root = Path(tempfile.mkdtemp(prefix="tokopt-bench-codesearch-"))
    try:
        make_repo(root, n_files)
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), TOKOPT_HOME=str(root / ".tokopt-home"))
        timings = {}
        for command in ("symbol", "refs"):
            start = time.perf_counter()
            p = subprocess.run([sys.executable, "-m", "tokopt", command, TARGET], cwd=root, env=env,
                               capture_output=True, timeout=120)
            timings[command] = time.perf_counter() - start
            if p.returncode != 0:
                raise RuntimeError(f"tokopt {command} failed: {p.stderr.decode(errors='replace')}")
        return timings
    finally:
        _remove(root)


def _remove(path: Path, tries: int = 5) -> None:
    """rmtree that waits out a straggler still writing (TemporaryDirectory's ignore_cleanup_errors is 3.10+)."""
    for attempt in range(tries):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == tries - 1:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(0.5 * (attempt + 1))


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    timings = run(n)
    for command, seconds in timings.items():
        print(f"tokopt {command:<6} on {n} files: {seconds:.2f} s")
    return 0 if all(s < LIMIT for s in timings.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
