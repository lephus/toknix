"""P1-T4: `toknix run` and `toknix recall` end to end (A2.1, A2.7, N2.1)."""
from __future__ import annotations

import importlib.util
import io
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support

from toknix import cli, runner

FOOTER_RE = re.compile(r"\[toknix: \d+ → \d+ lines; full output: toknix recall ([0-9a-f]+); exit (\d+)\]")
PY = shlex.quote(sys.executable)
TIMING_RE = re.compile(rb"\d+\.\d+s")  # "Ran 42 tests in 0.001s", "2 failed, 40 passed in 0.05s"


class TempHome(unittest.TestCase):
    """HOME and TOKNIX_HOME in a temp dir; toknix runs as a real process from the repo root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / ".toknix"
        env = {k: v for k, v in os.environ.items() if not k.startswith("TOKNIX_")}
        self.env = dict(env, PYTHONPATH=str(support.SRC), TOKNIX_HOME=str(self.home), HOME=self.tmp.name,
                        USERPROFILE=self.tmp.name, PYTHONDONTWRITEBYTECODE="1")

    def toknix(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "toknix", *args], cwd=support.ROOT,
                              capture_output=True, env=dict(self.env, **env))

    def run_and_footer(self, cmd: str) -> tuple[subprocess.CompletedProcess, str]:
        p = self.toknix("run", "--", cmd)
        m = FOOTER_RE.search(p.stdout.decode("utf-8"))
        self.assertIsNotNone(m, p.stdout.decode("utf-8", "replace")[-500:])
        self.assertEqual(int(m.group(2)), p.returncode)
        return p, m.group(1)

    def rows(self, sql: str) -> list[tuple]:
        con = sqlite3.connect(str(self.home / "toknix.db"))
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()


class TestExitCode(TempHome):
    def test_exit_code_is_kept(self):
        for code in (0, 1, 3, 101):
            with self.subTest(code=code):
                self.assertEqual(self.toknix("run", "--", f"exit {code}").returncode, code)

    def test_output_and_exit_code_of_a_small_command(self):
        p = self.toknix("run", "--", "echo hi; exit 7")
        self.assertEqual((p.returncode, p.stdout), (7, b"hi\n"))

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_killed_by_signal_reports_128_plus_signal(self):
        self.assertEqual(self.toknix("run", "--", "kill -9 $$").returncode, 137)

    def test_without_command_is_a_usage_error(self):
        p = self.toknix("run")
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"usage", p.stderr)


class TestCommandLine(TempHome):
    def test_one_argument_is_the_whole_command(self):  # how hook bash wraps (A3)
        self.assertEqual(self.toknix("run", "--", "echo abc | tr a x").stdout, b"xbc\n")

    def test_several_arguments_keep_their_quoting(self):
        self.assertEqual(self.toknix("run", "--", "echo", "a  b", "$HOME").stdout, b"a  b $HOME\n")

    def test_double_dash_is_optional(self):
        self.assertEqual(self.toknix("run", "echo", "hi").stdout, b"hi\n")


class TestSuiteEndToEnd(TempHome):
    """Plan: a suite with 2 failing tests prints the recall footer; recall prints the original output."""

    def direct(self, cmd: str) -> bytes:
        env = dict(self.env, **runner.QUIET_ENV)
        return subprocess.run(runner.shell_argv(cmd), cwd=support.ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout

    def check_suite(self, cmd: str, kind: str, keep: list[str]):
        p, tee_id = self.run_and_footer(cmd)
        out = p.stdout.decode("utf-8")
        self.assertEqual(p.returncode, 1)
        for k in keep:
            self.assertIn(k, out)
        r = self.toknix("recall", tee_id)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(len(p.stdout), len(r.stdout) * 0.9)
        self.assertEqual(TIMING_RE.sub(b"Xs", r.stdout), TIMING_RE.sub(b"Xs", self.direct(cmd)))
        self.assertEqual(self.rows("SELECT kind, exit, tee_id FROM runs"), [(kind, 1, tee_id)])

    def test_unittest_suite(self):
        self.check_suite(f"{PY} -m unittest discover -s tests/fixtures_py -v", "generic",
                         ["FAIL: test_fixed_discount", "ERROR: test_missing_qty", "AssertionError: 90 != 80",
                          "FAILED (failures=1, errors=1)"])

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed (toknix never needs it)")
    def test_pytest_suite(self):
        self.check_suite(f"{PY} -m pytest tests/fixtures_py", "pytest",
                         ["test_fixed_discount", "test_missing_qty", "2 failed, 40 passed"])


class TestRecall(TempHome):
    def test_recall_is_byte_exact(self):
        fixture = support.FIXTURES / "build_longlines.txt"
        _, tee_id = self.run_and_footer("cat tests/fixtures/build_longlines.txt; printf 'crlf\\r\\nno newline'")
        self.assertEqual(self.toknix("recall", tee_id).stdout, fixture.read_bytes() + b"crlf\r\nno newline")

    def test_footer_line_count_matches_recall(self):  # the agent pages with --lines using that count
        p, tee_id = self.run_and_footer("cat tests/fixtures/build_longlines.txt")
        n = int(re.search(r"\[toknix: (\d+) →", p.stdout.decode("utf-8")).group(1))
        self.assertEqual(n, len((support.FIXTURES / "build_longlines.txt").read_bytes().splitlines()))
        self.assertEqual(self.toknix("recall", tee_id, "--lines", f"{n}:").stdout,
                         b"make: *** [Makefile:14: typecheck] Error 2\n")

    def test_recall_is_recorded(self):
        _, tee_id = self.run_and_footer("cat tests/fixtures/build_longlines.txt")
        self.toknix("recall", tee_id)
        self.toknix("recall", tee_id, "--grep", "error")
        rows = self.rows("SELECT ts, tee_id FROM recalls")
        self.assertEqual([r[1] for r in rows], [tee_id, tee_id])
        self.assertTrue(all(isinstance(r[0], float) for r in rows))

    def test_unknown_id(self):
        p = self.toknix("recall", "ffffff")
        self.assertEqual((p.returncode, p.stdout), (1, b""))
        self.assertIn(b"no saved output 'ffffff'", p.stderr)
        self.assertEqual([r[0] for r in self.rows("SELECT tee_id FROM recalls")], ["ffffff"])

    def test_ids_never_escape_the_tee_dir(self):
        (self.home / "secret.log").parent.mkdir(parents=True)
        (self.home / "secret.log").write_text("x")
        p = self.toknix("recall", "../secret")
        self.assertEqual((p.returncode, p.stdout), (1, b""))


class TestRecallFilters(unittest.TestCase):
    """--grep and --lines, in process (A2.7)."""

    TEE = "line one\n\x1b[31merror: first\x1b[0m\nline three\nerror: second\nprogress 10%\rprogress 100%\n"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"TOKNIX_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        (Path(tmp.name) / "tee").mkdir()
        (Path(tmp.name) / "tee" / "abc123.log").write_bytes(self.TEE.encode("utf-8"))

    def recall(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["recall", "abc123", *args])
        return code, out.getvalue(), err.getvalue()

    def test_grep_prints_numbered_clean_lines(self):
        self.assertEqual(self.recall("--grep", "error"), (0, "2: error: first\n4: error: second\n", ""))

    def test_lines_range_is_one_based_inclusive(self):
        self.assertEqual(self.recall("--lines", "2:3")[1], "error: first\nline three\n")
        self.assertEqual(self.recall("--lines", "4:")[1], "error: second\nprogress 100%\n")
        self.assertEqual(self.recall("--lines", ":1")[1], "line one\n")
        self.assertEqual(self.recall("--lines", "3")[1], "line three\n")

    def test_grep_within_lines_keeps_original_numbers(self):
        self.assertEqual(self.recall("--lines", "3:5", "--grep", "error")[1], "4: error: second\n")

    def test_bad_arguments_are_usage_errors(self):
        for args in (["--grep", "("], ["--lines", "a:b"], ["--lines", "0:2"]):
            with self.subTest(args=args):
                code, out, err = self.recall(*args)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("toknix recall:", err)


class TestRawAndRobustness(TempHome):
    def test_toknix_raw_prints_verbatim(self):
        fixture = support.FIXTURES / "pytest_fail.txt"
        p = self.toknix("run", "--", "cat tests/fixtures/pytest_fail.txt; exit 1", TOKNIX_RAW="1")
        self.assertEqual((p.returncode, p.stdout), (1, fixture.read_bytes()))
        self.assertFalse((self.home / "tee").exists() and any((self.home / "tee").iterdir()))

    def test_output_is_utf8_whatever_the_console_encoding(self):
        p = self.toknix("run", "--", "cat tests/fixtures/build_longlines.txt", PYTHONIOENCODING="ascii")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout.decode("utf-8")
        self.assertIn("⚡ Done in 412ms", out)
        self.assertRegex(out, FOOTER_RE)

    def test_broken_toknix_home_never_breaks_the_command(self):
        broken = Path(self.tmp.name) / "not-a-dir"
        broken.write_text("x")
        p = self.toknix("run", "--", "cat tests/fixtures/pytest_fail.txt; exit 1", TOKNIX_HOME=str(broken))
        self.assertEqual((p.returncode, p.stderr), (1, b""))
        self.assertIn(b"2 failed, 40 passed", p.stdout)
        self.assertNotIn(b"toknix recall", p.stdout)  # no tee could be saved, so no footer

    def test_filter_crash_falls_back_to_raw_output(self):
        text = "".join(f"line {i}\n" for i in range(100))
        done = subprocess.CompletedProcess([], 4, stdout=text.encode("utf-8"))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"TOKNIX_HOME": str(self.home)}), \
                mock.patch.object(runner.subprocess, "run", return_value=done), \
                mock.patch.object(runner.filters, "apply", side_effect=RuntimeError("boom")), \
                redirect_stdout(out), redirect_stderr(err):
            code = runner.run("make")
        self.assertEqual((code, out.getvalue(), err.getvalue()), (4, text, ""))


class TestFindBash(unittest.TestCase):
    """Agents write bash; on Windows that must be Git Bash, never WSL's System32\\bash.exe (A2.1)."""

    @staticmethod
    def find(windows: bool, env: dict, which: dict, files: set) -> str:
        return runner.find_bash(windows=windows, env=env, which=which.get, exists=lambda p: str(p) in files)

    def test_toknix_shell_wins(self):
        self.assertEqual(self.find(False, {"TOKNIX_SHELL": "/opt/bash5"}, {"bash": "/bin/bash"}, set()), "/opt/bash5")

    def test_posix(self):
        self.assertEqual(self.find(False, {}, {"bash": "/usr/bin/bash"}, set()), "/usr/bin/bash")
        self.assertEqual(self.find(False, {}, {}, set()), "/bin/sh")

    def test_windows_claude_code_git_bash_path(self):
        p = r"D:\tools\Git\bin\bash.exe"
        self.assertEqual(self.find(True, {"CLAUDE_CODE_GIT_BASH_PATH": p}, {}, {p}), p)

    def test_windows_bash_next_to_git(self):
        git = r"C:\Program Files\Git\cmd\git.exe"
        bash = r"C:\Program Files\Git\bin\bash.exe"
        wsl = r"C:\Windows\System32\bash.exe"
        self.assertEqual(self.find(True, {}, {"git": git, "bash": wsl}, {bash}), bash)

    def test_windows_never_picks_wsl_bash(self):
        wsl = r"C:\Windows\System32\bash.exe"
        self.assertEqual(self.find(True, {}, {"bash": wsl}, set()), "bash")
        mingw = r"C:\msys64\usr\bin\bash.exe"
        self.assertEqual(self.find(True, {}, {"bash": mingw}, set()), mingw)


if __name__ == "__main__":
    unittest.main()
