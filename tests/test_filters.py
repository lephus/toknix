"""P1-T3: filters on real fixtures (A2.4–A2.6, "Kiểm chứng A2") and the absolute ERROR_RE invariant."""
from __future__ import annotations

import re
import unittest

import support  # noqa: F401  (puts src/ on sys.path)

from tokopt import config, filters
from tokopt.filters import ERROR_RE, apply, budget_cut, f_build, f_lint
from tokopt.textutil import normalize

# fixture -> (command that produced it, exit code)
FIXTURES = {
    "pytest_fail.txt": ("python -m pytest -v", 1),
    "pytest_pass.txt": ("python -m pytest -q test_m1.py test_m2.py", 0),
    "pip_install.txt": ("pip install --no-cache-dir --target pipt requests==2.31.0 rich==13.7.1", 0),
    "npm_install.txt": ("npm install express@4.19.2 typescript@5.4.5 eslint@8.57.0 --no-fund", 0),
    "tsc_errors.txt": ("npx tsc --noEmit --strict a.ts", 2),
    "git_log.txt": ("git log", 0),
    "git_status.txt": ("git status", 0),
    "git_diff_small.txt": ("git diff", 0),
    "jest_fail.txt": ("npx jest --verbose", 1),
    "gotest_fail.txt": ("go test -v ./...", 1),
    "cargotest_fail.txt": ("cargo test", 101),
    "eslint.txt": ("npx eslint src", 1),
    "build_longlines.txt": ("make build", 2),
    "build_timestamps.txt": ("./gradlew bootRun", 1),
}

OMITTED_RE = re.compile(r"^  … \d+ lines omitted …$")


def cfg() -> dict:
    return dict(config.DEFAULTS)


def read(name: str) -> list[str]:
    return normalize((support.FIXTURES / name).read_text(encoding="utf-8"))


def run_fixture(name: str, exit_code: int | None = None) -> tuple[list[str], list[str], str]:
    cmd, code = FIXTURES[name]
    lines = read(name)
    r = apply(cmd, lines, code if exit_code is None else exit_code, cfg())
    return lines, r.lines, r.kind


def kept_verbatim(line: str, out: list[str]) -> bool:
    """Present as-is, or folded by dedupe_runs into `line  [×N]` (A2.4 exception)."""
    return any(o == line or (o.startswith(line + "  [×") and o.endswith("]")) for o in out)


class TestFixtureConditions(unittest.TestCase):
    """The table "Kiểm chứng A2" in docs/ALGORITHMS.md."""

    def test_pytest_fail(self):
        lines, out, kind = run_fixture("pytest_fail.txt")
        text = "\n".join(out)
        self.assertEqual(kind, "pytest")
        self.assertIn("test_parse_missing", text)
        self.assertIn("test_math", text)
        self.assertTrue(any("assert" in l and "== 7" in l for l in out))
        self.assertIn("2 failed, 40 passed", text)
        self.assertFalse(any(re.search(r"\bPASSED\b", l) for l in out))
        self.assertLessEqual(len(out), len(lines) // 2)

    def test_pytest_pass_is_passthrough(self):
        c = cfg()
        lines, out, _ = run_fixture("pytest_pass.txt")
        text = (support.FIXTURES / "pytest_pass.txt").read_text(encoding="utf-8")
        self.assertLessEqual(len(lines), c["pass_through_lines"])
        self.assertLessEqual(len(text), c["pass_through_chars"])
        self.assertEqual(out, [l for l in lines if l])

    def test_pip_install(self):
        _, out, kind = run_fixture("pip_install.txt")
        self.assertEqual(kind, "install")
        self.assertFalse(any(re.match(r"\s*(Collecting|Downloading)\b", l) for l in out))
        self.assertTrue(any(l.startswith("Successfully installed") for l in out))

    def test_pip_install_drops_progress_bars(self):  # BUG-1
        _, out, _ = run_fixture("pip_install.txt")
        self.assertEqual([l for l in out if "━" in l], [])

    def test_npm_install(self):
        _, out, kind = run_fixture("npm_install.txt")
        self.assertEqual(kind, "install")
        merged = [l for l in out if l.startswith("[tokopt] 6 deprecation warnings:")]
        self.assertEqual(len(merged), 1)
        self.assertFalse(any("npm warn deprecated" in l for l in out))
        self.assertTrue(any("added 172 packages" in l for l in out))
        self.assertTrue(any("7 vulnerabilities" in l for l in out))

    def test_git_log(self):
        _, out, kind = run_fixture("git_log.txt")
        self.assertEqual(kind, "gitlog")
        self.assertLessEqual(len(out), 31)
        self.assertRegex(out[0], r"^[0-9a-f]{9} .+: .+")

    def test_git_log_reports_omitted_commits(self):  # BUG-2
        _, out, _ = run_fixture("git_log.txt")
        self.assertTrue(out[-1].startswith("[tokopt] 95 older commits omitted"), out[-1])

    def test_git_status(self):
        _, out, kind = run_fixture("git_status.txt")
        self.assertEqual(kind, "gitstatus")
        self.assertFalse(any(l.strip().startswith('(use "git') for l in out))
        self.assertTrue(any("modified:   README.md" in l for l in out))
        self.assertTrue(out[-1].startswith("no changes added to commit"))  # summary line stays


class TestHandWrittenFixtures(unittest.TestCase):
    def test_jest(self):
        _, out, kind = run_fixture("jest_fail.txt")
        text = "\n".join(out)
        self.assertEqual(kind, "jest")
        self.assertFalse(any(l.strip().startswith(("✓", "PASS ")) for l in out))
        self.assertIn("FAIL src/cart/total.test.js", text)
        self.assertIn("✕ applies fixed discount", text)
        self.assertIn("● cart total › applies fixed discount", text)
        self.assertIn("Received: 100", text)
        self.assertIn("Tests:       1 failed, 11 passed, 12 total", text)
        self.assertIn("Test Suites: 1 failed, 2 passed, 3 total", text)

    def test_gotest(self):
        _, out, kind = run_fixture("gotest_fail.txt")
        text = "\n".join(out)
        self.assertEqual(kind, "gotest")
        self.assertFalse(any(re.match(r"=== (RUN|PAUSE|CONT)|\s*--- PASS:|PASS$|ok\s", l) for l in out))
        self.assertIn("--- FAIL: TestMerge/defaults", text)
        self.assertIn("merge_test.go:41: Merge() port = 8080, want 9090", text)
        self.assertIn("panic: runtime error: invalid memory address", text)
        self.assertIn("handler.go:58", text)
        self.assertEqual(out[-1], "FAIL")

    def test_cargotest(self):
        _, out, kind = run_fixture("cargotest_fail.txt")
        text = "\n".join(out)
        self.assertEqual(kind, "cargotest")
        self.assertFalse(any(re.match(r"\s*Compiling ", l) or l.endswith(" ... ok") for l in out))
        self.assertIn("test tests::div_by_zero ... FAILED", text)
        self.assertIn("thread 'tests::parse_negative' panicked at src/lib.rs:51:9:", text)
        self.assertIn("  left: Some(-3)", text)
        self.assertIn("test result: FAILED. 6 passed; 2 failed", text)

    def test_eslint_stylish_keeps_every_diagnostic(self):
        lines, out, kind = run_fixture("eslint.txt")
        self.assertEqual(kind, "lint")
        diags = [l for l in lines if re.match(r"\s+\d+:\d+\s+(error|warning)\s", l)]
        self.assertEqual(len(diags), 5)
        for d in diags:
            self.assertIn(d, out)
        self.assertIn("✖ 5 problems (3 errors, 2 warnings)", out)


class TestErrorInvariant(unittest.TestCase):
    """Every ERROR_RE line of the input is in the output verbatim (A2.4, absolute)."""

    def test_every_fixture_keeps_error_lines(self):
        for name in FIXTURES:
            for exit_code in (0, 1):
                with self.subTest(fixture=name, exit_code=exit_code):
                    lines, out, kind = run_fixture(name, exit_code)
                    merged_deprecations = kind == "install" and any(
                        re.match(r"\[tokopt\] \d+ deprecation warnings:", o) for o in out)
                    for l in lines:
                        if not ERROR_RE.search(l):
                            continue
                        if merged_deprecations and re.search(r"(?i)\bdeprecated\b", l):
                            continue  # the other A2.4 exception: folded into one line
                        self.assertTrue(kept_verbatim(l, out), f"lost: {l!r}")

    def test_five_hundred_failures_survive_the_budget(self):
        lines = []
        for i in range(500):
            lines.append(f"tests/test_x.py::test_case_{i} FAILED")
            lines.append(f"  some context line number {i} for case {i} " + "x" * (i % 7))
        out = apply("some-tool --check", lines, 1, cfg()).lines
        failed = [l for l in lines if "FAILED" in l]
        self.assertEqual(len(failed), 500)
        for l in failed:
            self.assertIn(l, out)


class TestBudgetCut(unittest.TestCase):
    def test_under_budget_is_unchanged(self):
        lines = [f"line {i}" for i in range(50)]
        self.assertEqual(budget_cut(lines, 120, 3000, 30), lines)

    def test_keeps_head_and_tail_with_gap_marker(self):
        lines = [f"line {i}" for i in range(200)]
        out = budget_cut(lines, 120, 3000, 30)
        self.assertEqual(out[:30], lines[:30])
        self.assertEqual(out[30], "  … 100 lines omitted …")
        self.assertEqual(out[31:], lines[130:])

    def test_error_lines_do_not_count_against_budget(self):
        lines = [f"line {i}" for i in range(300)]
        for i in range(40, 260, 2):  # 110 error lines in the middle
            lines[i] = f"error: case {i} broke"
        out = budget_cut(lines, 120, 3000, 30)
        errors = [l for l in lines if ERROR_RE.search(l)]
        for l in errors:
            self.assertIn(l, out)
        rest = [l for l in out if not ERROR_RE.search(l) and not OMITTED_RE.match(l)]
        self.assertLessEqual(len(rest), 120)

    def test_small_budget_shrinks_tail_then_head(self):
        lines = [f"line {i}" for i in range(200)]
        out = budget_cut(lines, 30, 3000, 30)
        rest = [l for l in out if not OMITTED_RE.match(l)]
        self.assertLessEqual(len(rest), 30)
        self.assertEqual(out[0], "line 0")
        self.assertEqual(out[-1], "line 199")


class TestBuild(unittest.TestCase):
    def test_docker_layer_status_only(self):  # found by build_timestamps.txt (P1-T3b)
        lines = ["a2abf6c4d29d: Waiting", "a2abf6c4d29d: Pull complete", "e1f3b4c5d6a7: Pulling fs layer",
                 "GracefulShutdown : Commencing graceful shutdown. Waiting for active requests to complete",
                 "Download complete: wrote dist/app.tar"]
        self.assertEqual(f_build(lines, False), lines[3:])


class TestLint(unittest.TestCase):
    def test_cap_applies_only_to_non_error_lines(self):
        lines = []
        for i in range(20):
            lines.append(f"app/models.py:{i + 1}: error: Incompatible types in assignment")
            lines.append(f"app/models.py:{i + 1}: note: See https://mypy.readthedocs.io/en/stable/x.html")
        out = f_lint(lines, True)
        self.assertEqual(sum(1 for l in out if ": error:" in l), 20)
        self.assertEqual(sum(1 for l in out if ": note:" in l), 15)
        self.assertEqual(out[-1], "[tokopt] 40 diagnostics in 1 files")

    def test_cap_is_per_file(self):
        lines = [f"{f}:{i}: note: hint {i}" for f in ("a.py", "b.py") for i in range(1, 21)]
        out = f_lint(lines, False)
        for f in ("a.py", "b.py"):
            self.assertEqual(sum(1 for l in out if l.startswith(f + ":")), 15)


if __name__ == "__main__":
    unittest.main()
