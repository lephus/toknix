"""P1-T3b: token-aware filters (N1.2b–d; A2.5b, A2.5c, A2.6) and the new rows of "Kiểm chứng A2"."""
import io
import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from tokopt import config, runner, store
from tokopt.filters import ERROR_RE, apply, budget_cut, cut_long_lines, f_gitdiff, normalize_costly
from tokopt.textutil import est_tokens, normalize
from test_filters import FIXTURES, OMITTED_RE, cfg, read, run_fixture

TS_RE = re.compile(r"^\s*\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2})")
HEX64 = "4a7f1c9e2b3d5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8"
SHA512 = "sha512-" + "Qm9vdHN0cmFwIGNvbXBsZXRlZCBpbiAzLjJz" * 2 + "Ab12=="


def para(i: int) -> str:
    """A 200-char default-class line: est_tokens == 50."""
    return (f"{i:04d} " + "lorem ipsum dolor " * 12)[:200]


def distinct(i: int) -> str:
    """Like para(), but no two lines share a shape, so collapse_similar keeps them all."""
    tag = "".join(chr(97 + (i // 26 ** k) % 26) for k in range(3))
    return (f"step {tag} " + "lorem ipsum dolor " * 12)[:200]


class TestConfig(unittest.TestCase):
    def test_new_defaults(self):
        d = config.DEFAULTS
        self.assertEqual(d["max_tokens"], 3000)
        self.assertEqual(d["max_line_chars"], 800)
        self.assertIs(d["normalize_hashes"], True)
        self.assertIs(d["normalize_timestamps"], True)
        self.assertIs(d["normalize_paths"], False)


class TestBudgetCutTokens(unittest.TestCase):
    def test_line_helper_is_fifty_tokens(self):
        self.assertEqual(len(para(1)), 200)
        self.assertEqual(est_tokens(para(1)), 50)

    def test_token_ceiling_cuts_under_line_ceiling(self):
        lines = [para(i) for i in range(100)]  # 100 lines < 120, but 5000 tokens > 3000
        out = budget_cut(lines, 120, 3000, 30)
        self.assertLessEqual(sum(est_tokens(l) for l in out), 3000 * 1.05)  # the gap marker counts too
        # tail shrinks first (head stays 30): 30·50 + t·50 + 8 (marker) ≤ 3150 → t = 32
        self.assertEqual(out[:30], lines[:30])
        self.assertEqual(out[30], "  … 38 lines omitted …")
        self.assertEqual(out[31:], lines[-32:])

    def test_head_shrinks_after_tail_reaches_five(self):
        lines = [para(i) for i in range(100)]
        out = budget_cut(lines, 120, 500, 30)  # 10 lines fit: tail 5, head 5
        self.assertEqual(out, lines[:5] + ["  … 90 lines omitted …"] + lines[-5:])

    def test_under_both_ceilings_is_unchanged(self):
        lines = [para(i) for i in range(50)]  # 2500 tokens
        self.assertEqual(budget_cut(lines, 120, 3000, 30), lines)

    def test_uses_given_token_ratios(self):
        lines = [para(i) for i in range(50)]
        out = budget_cut(lines, 120, 3000, 30, ratios={"default": 2.0})  # now 5000 tokens
        self.assertLess(len(out), len(lines))

    def test_error_lines_are_outside_the_token_budget(self):
        lines = [para(i) for i in range(100)]
        for i in range(0, 100, 3):
            lines[i] = "error: " + lines[i]
        out = budget_cut(lines, 120, 1000, 30)
        for l in lines:
            if ERROR_RE.search(l):
                self.assertIn(l, out)
        rest = [l for l in out if not ERROR_RE.search(l) and not OMITTED_RE.match(l)]
        self.assertLessEqual(sum(est_tokens(l) for l in rest), 1000 * 1.05)


class TestCutLongLines(unittest.TestCase):
    def test_long_line_keeps_head_and_tail(self):
        line = "".join(chr(97 + i % 26) for i in range(1000))
        out = cut_long_lines([line], 800)
        self.assertEqual(out, [f"{line[:300]} …[500 chars]… {line[-200:]}"])
        self.assertLessEqual(len(out[0]), 520)

    def test_line_at_limit_is_unchanged(self):
        self.assertEqual(cut_long_lines(["x" * 800], 800), ["x" * 800])

    def test_error_line_is_never_cut(self):
        line = "error: " + "y" * 1500
        self.assertEqual(cut_long_lines([line], 800), [line])


class TestNormalizeCostly(unittest.TestCase):
    def test_long_hex_is_shortened(self):
        out = normalize_costly([f"#12 writing image sha256:{HEX64} done"], "build", cfg())
        self.assertEqual(out, ["#12 writing image sha256:4a7f1c9e2b3d… done"])

    def test_hex_under_32_chars_is_kept(self):
        line = "commit " + HEX64[:31]
        self.assertEqual(normalize_costly([line], "build", cfg()), [line])

    def test_sha_base64_integrity_is_shortened(self):
        out = normalize_costly([f"lockfile: integrity {SHA512} resolved"], "install", cfg())
        self.assertEqual(out, ["lockfile: integrity sha512-… resolved"])

    def test_error_line_keeps_its_hashes(self):  # checksum errors need both digests verbatim
        line = f"npm error code EINTEGRITY: expected {SHA512} but got sha256:{HEX64}"
        self.assertEqual(normalize_costly([line], "install", cfg()), [line])

    def test_only_install_build_gitnet(self):
        line = f"blob {HEX64}"
        for kind in ("install", "build", "gitnet"):
            self.assertNotEqual(normalize_costly([line], kind, cfg()), [line], kind)
        for kind in ("pytest", "jest", "gotest", "cargotest", "lint", "compile", "gitdiff",
                     "gitstatus", "gitlog", "listing", "generic"):
            self.assertEqual(normalize_costly([line], kind, cfg()), [line], kind)

    def test_flags_turn_transforms_off(self):
        c = dict(cfg(), normalize_hashes=False, normalize_timestamps=False)
        lines = [f"10:00:0{i} pulled {HEX64}" for i in range(6)]
        self.assertEqual(normalize_costly(lines, "build", c), lines)

    def test_hh_mm_ss_run_keeps_first_and_last_timestamp(self):
        lines = ["[09:12:01] Using gulpfile ~/web/gulpfile.js", "[09:12:01] Starting 'build'...",
                 "[09:12:02] Starting 'styles'...", "[09:12:04] Finished 'styles' after 1.9 s",
                 "[09:12:04] Finished 'build' after 3.1 s"]
        out = normalize_costly(lines, "build", cfg())
        self.assertEqual(out, [lines[0], "Starting 'build'...", "Starting 'styles'...",
                               "Finished 'styles' after 1.9 s", lines[4]])

    def test_run_of_three_is_unchanged(self):
        lines = ["2026-10-17 09:12:01 a", "2026-10-17 09:12:02 b", "2026-10-17 09:12:03 c", "done"]
        self.assertEqual(normalize_costly(lines, "build", cfg()), lines)

    def test_paths_off_by_default(self):
        line = "/home/dev/shop/build/libs/shop.jar written"
        self.assertEqual(normalize_costly([line], "build", cfg(), cwd="/home/dev/shop"), [line])

    def test_paths_under_cwd_become_relative(self):
        c = dict(cfg(), normalize_paths=True)
        out = normalize_costly(["wrote /home/dev/shop/build/libs/shop.jar and /home/dev/other/x.jar"],
                               "build", c, cwd="/home/dev/shop")
        self.assertEqual(out, ["wrote build/libs/shop.jar and /home/dev/other/x.jar"])


class TestGitdiffTokenBudget(unittest.TestCase):
    @staticmethod
    def block(name: str, n: int, width: int = 80) -> list[str]:
        body = [("+" + f"{name} line {i} " + "abc def " * (width // 8 + 1))[:width] for i in range(n)]
        return [f"diff --git a/{name} b/{name}", f"--- a/{name}", f"+++ b/{name}", "@@ -1,0 +1,%d @@" % n] + body

    def test_token_ceiling_lists_remaining_files(self):
        lines = self.block("a.py", 100) + self.block("b.py", 100) + self.block("c.py", 100)  # ~6000 tokens
        out = f_gitdiff(lines, False, max_tokens=3000)
        self.assertEqual(out[:104], lines[:104])
        self.assertIn("[tokopt] 2 more files changed; view one with `git diff -- <file>`:", out)
        self.assertIn("  b.py (+100 -0)", out)
        self.assertIn("  c.py (+100 -0)", out)

    def test_block_alone_over_budget_is_listed(self):
        minified = self.block("dist/app.min.js", 1, width=20000)
        lines = minified + self.block("src/app.js", 10)
        out = f_gitdiff(lines, False, max_tokens=3000)
        self.assertNotIn(minified[-1], out)
        self.assertIn("  dist/app.min.js (+1 -0)", out)
        self.assertEqual(out[:14], self.block("src/app.js", 10))

    def test_apply_passes_max_tokens_and_skips_line_cuts(self):
        lines = self.block("a.py", 100) + self.block("b.py", 100) + self.block("c.py", 100)
        r = apply("git diff", lines, 0, cfg())
        self.assertEqual(r.kind, "gitdiff")
        self.assertIn("  c.py (+100 -0)", r.lines)
        long_line = self.block("x.py", 1, width=1200)
        self.assertEqual(apply("git diff", long_line, 0, cfg()).lines, long_line)


class TestApplyResult(unittest.TestCase):
    def test_error_overflow(self):
        lines = [f"FAILED tests/test_checkout.py::TestCart::test_case_{i} - AssertionError: expected 1, got 2"
                 for i in range(500)]
        r = apply("some-tool --check", lines, 1, cfg())
        self.assertTrue(r.error_overflow)
        for l in lines:
            self.assertIn(l, r.lines)
        self.assertFalse(apply("some-tool --check", lines[:20], 1, cfg()).error_overflow)

    def test_lossy_when_a_line_is_cut(self):
        lines = [f"step {i}" for i in range(50)] + ["z" * 900]
        self.assertTrue(apply("make", lines, 0, cfg()).lossy)
        self.assertFalse(apply("make", lines[:50], 0, cfg()).lossy)

    def test_lossy_when_normalized(self):
        lines = [f"step {i}" for i in range(50)] + [f"layer sha256:{HEX64}"]
        self.assertTrue(apply("docker build .", lines, 0, cfg()).lossy)

    def test_non_error_output_within_token_budget(self):
        """"Kiểm chứng A2": every fixture but gitdiff, plus a large synthetic generic output."""
        cases = [(n, *FIXTURES[n]) for n in FIXTURES if n != "git_diff_small.txt"]
        for name, cmd, code in cases + [("synthetic", "some-tool", 0), ("synthetic", "some-tool", 1)]:
            lines = [distinct(i) for i in range(2000)] if name == "synthetic" else read(name)
            with self.subTest(fixture=name, exit_code=code):
                out = apply(cmd, lines, code, cfg()).lines
                rest = sum(est_tokens(l) for l in out if not ERROR_RE.search(l))
                self.assertLessEqual(rest, 3000 * (2 if code else 1) * 1.05)


class TestLongLinesFixture(unittest.TestCase):
    def test_build_longlines(self):
        lines, out, kind = run_fixture("build_longlines.txt")
        self.assertEqual(kind, "build")
        long_ok = [l for l in lines if len(l) > 800 and not ERROR_RE.search(l)]
        long_err = [l for l in lines if len(l) > 800 and ERROR_RE.search(l)]
        self.assertEqual((len(long_ok), len(long_err)), (3, 1))
        for l in long_ok:
            self.assertNotIn(l, out)
            self.assertIn(f"{l[:300]} …[{len(l) - 500} chars]… {l[-200:]}", out)
        self.assertIn(long_err[0], out)
        self.assertTrue(all(len(l) <= 520 for l in out if not ERROR_RE.search(l)))
        self.assertIn("make: *** [Makefile:14: typecheck] Error 2", out)


class TestTimestampsFixture(unittest.TestCase):
    def test_build_timestamps(self):
        lines = read("build_timestamps.txt")
        out = normalize_costly(lines, "build", cfg())
        self.assertEqual(len(out), len(lines))
        runs, i = [], 0
        while i < len(lines):
            j = i
            while j < len(lines) and TS_RE.match(lines[j]):
                j += 1
            if j > i:
                runs.append((i, j - 1))
            i = max(j, i + 1)
        self.assertEqual([b - a + 1 for a, b in runs], [21, 5, 3])
        for a, b in runs:
            for k in range(a, b + 1):
                keep = b - a + 1 < 4 or k in (a, b) or ERROR_RE.search(lines[k])
                if keep:
                    self.assertEqual(out[k], lines[k])
                else:
                    self.assertEqual(out[k], lines[k].split(None, 1)[1])
                    self.assertTrue(out[k].startswith("INFO 4211 ---"), out[k])
        self.assertEqual(sum(1 for l in out if TS_RE.match(l)), 8)  # 2 + 1 WARN, 2, 3

    def test_through_apply(self):
        _, out, kind = run_fixture("build_timestamps.txt")
        self.assertEqual(kind, "build")
        self.assertEqual(sum(1 for l in out if TS_RE.match(l)), 8)
        self.assertIn("BUILD FAILED in 43s", out)


class TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"TOKOPT_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def rows(self, cols: str) -> list[tuple]:
        con = sqlite3.connect(os.path.join(self.tmp.name, "tokopt.db"))
        try:
            return con.execute(f"SELECT {cols} FROM runs").fetchall()
        finally:
            con.close()


class TestStore(TempHome):
    def test_record_run_stores_error_overflow(self):
        store.record_run("generic", "x", 1, 100, 50, 10, 5, "abc123", 1)
        self.assertEqual(self.rows("tee_id, error_overflow"), [("abc123", 1)])

    def test_old_runs_table_gains_the_column(self):
        con = sqlite3.connect(os.path.join(self.tmp.name, "tokopt.db"))
        con.execute("CREATE TABLE runs(ts REAL, kind TEXT, cmd TEXT, exit INTEGER, raw_chars INTEGER, "
                    "out_chars INTEGER, raw_lines INTEGER, out_lines INTEGER, tee_id TEXT)")
        con.execute("INSERT INTO runs VALUES(1, 'old', 'x', 0, 1, 1, 1, 1, '')")
        con.commit()
        con.close()
        store.record_run("generic", "y", 0, 100, 50, 10, 5, "")
        self.assertEqual(self.rows("kind, error_overflow"), [("old", 0), ("generic", 0)])


class TestRunner(TempHome):
    def run_with_output(self, cmd: str, text: str, code: int) -> str:
        done = subprocess.CompletedProcess([], code, stdout=text.encode("utf-8"))
        buf = io.StringIO()
        with mock.patch.object(runner.subprocess, "run", return_value=done), redirect_stdout(buf):
            self.assertEqual(runner.run(cmd), code)
        return buf.getvalue()

    def tee_ids(self) -> list[str]:
        d = os.path.join(self.tmp.name, "tee")
        return sorted(f[:-4] for f in os.listdir(d)) if os.path.isdir(d) else []

    def test_lossy_change_is_teed_even_when_small(self):
        text = "\n".join([distinct(i) for i in range(40)] + ["q" * 850]) + "\n"  # cut saves < 10 %
        printed = self.run_with_output("make", text, 0)
        ids = self.tee_ids()
        self.assertEqual(len(ids), 1)
        self.assertIn(f"tokopt recall {ids[0]}", printed)
        with open(os.path.join(self.tmp.name, "tee", ids[0] + ".log"), encoding="utf-8") as f:
            self.assertEqual(f.read(), text)

    def test_build_longlines_has_tee(self):
        text = (support.FIXTURES / "build_longlines.txt").read_text(encoding="utf-8")
        printed = self.run_with_output("make build", text, 2)
        self.assertEqual(len(self.tee_ids()), 1)
        self.assertIn("exit 2]", printed)

    def test_five_hundred_failures_set_error_overflow(self):
        lines = [f"FAILED tests/test_checkout.py::TestCart::test_case_{i} - AssertionError: expected 1, got 2"
                 for i in range(500)]
        printed = self.run_with_output("some-tool --check", "\n".join(lines) + "\n", 1)
        for l in lines:
            self.assertIn(l + "\n", printed)
        self.assertEqual(self.rows("error_overflow"), [(1,)])


if __name__ == "__main__":
    unittest.main()
