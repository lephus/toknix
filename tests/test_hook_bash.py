"""P2-T1: `toknix hook bash` (A3)."""
from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support

from toknix import cli, config, hooks, runner

# (command, wrap?)
WRAP_CASES = [
    # wrapped: the first program is in wrap_programs
    ("npm test", True),
    ("git status", True),
    ("pytest -x tests/", True),
    ("python -m pytest -q", True),
    ("uv run pytest", True),
    ("npx jest --ci", True),
    ("NODE_ENV=test npm test", True),
    ("cargo test", True),
    ("go test ./...", True),
    ("npm run build", True),
    ("ls -la src", True),
    ("npm test | tail -5", True),
    ("git log --oneline -n 50 && git status", True),
    ("git commit -m 'fix: handle \"quoted\" input'", True),
    ("  npm test  ", True),
    # not wrapped: not a noisy program
    ("echo hi", False),
    ("cat package.json", False),
    ("vim x", False),
    ("echo hi && npm test", False),  # only the first program counts
    ("", False),
    ("   ", False),
    # not wrapped: already toknix
    ("toknix run -- 'npm test'", False),
    ("toknix gain", False),
    ("tokopt run -- 'npm test'", False),  # the name toknix had for its first day
    ("TOKOPT_RAW=1 npm test", False),
    ("TOKNIX_RAW=1 npm test", False),
    # not wrapped: background, heredoc
    ("npm test &", False),
    ("cat <<'EOF' > notes.txt\nhi\nEOF", False),
    ("git commit -F - <<EOF\nmsg\nEOF", False),
    # not wrapped: changes the agent's shell state (cwd/env), wherever it appears
    ("cd x && npm test", False),
    ("npm test && cd dist", False),
    ("npm test; popd", False),
    ("npm test\ncd dist", False),
    ("npm test\nexport CI=1", False),
    ("export CI=1; npm test", False),
    ("source .venv/bin/activate && pytest", False),
    (". .venv/bin/activate && pytest", False),
    ("pushd sub && make && popd", False),
    ("ulimit -n 4096 && cargo test", False),
    ("alias ll='ls -l'; ll", False),
    ("set -e; npm test", False),
    ("exec npm test", False),
    ("unset CI || npm test", False),
]


def hook(tool_input: dict, **extra) -> dict | None:
    return hooks.hook_bash({"session_id": "s", "cwd": "/w", "tool_name": "Bash", "tool_input": tool_input, **extra})


class TempHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith("TOKNIX_")}
        patcher = mock.patch.dict(os.environ, dict(env, TOKNIX_HOME=tmp.name), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestShouldWrap(TempHome):
    def test_table(self):
        self.assertGreaterEqual(len(WRAP_CASES), 20)
        for cmd, wrap in WRAP_CASES:
            with self.subTest(cmd=cmd):
                self.assertEqual(hooks.should_wrap(cmd, {}, config.DEFAULTS), wrap)

    def test_run_in_background(self):
        self.assertTrue(hooks.should_wrap("npm test", {"run_in_background": False}, config.DEFAULTS))
        self.assertFalse(hooks.should_wrap("npm test", {"run_in_background": True}, config.DEFAULTS))
        self.assertIsNone(hook({"command": "npm test", "run_in_background": True}))

    def test_wrap_programs_come_from_config(self):
        (self.home / "config.json").write_text(json.dumps({"wrap_programs": ["make"]}))
        self.assertIsNotNone(hook({"command": "make all"}))
        self.assertIsNone(hook({"command": "npm test"}))


class TestReply(TempHome):
    def test_rewrites_command_and_keeps_other_fields(self):
        ti = {"command": "npm test -- --grep 'cart total'", "description": "Run tests", "timeout": 120000}
        self.assertEqual(hook(ti), {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {"command": "toknix run -- 'npm test -- --grep '\"'\"'cart total'\"'\"''",
                             "description": "Run tests", "timeout": 120000}}})

    def test_no_permission_decision_by_default(self):  # the user's permission rules still apply (A3)
        self.assertNotIn("permissionDecision", hook({"command": "git push --force"})["hookSpecificOutput"])

    def test_hook_permission_from_config(self):
        for value in ("allow", "ask"):
            with self.subTest(value=value):
                (self.home / "config.json").write_text(json.dumps({"hook_permission": value}))
                self.assertEqual(hook({"command": "npm test"})["hookSpecificOutput"]["permissionDecision"], value)
        (self.home / "config.json").write_text(json.dumps({"hook_permission": "deny"}))  # not a valid choice
        self.assertNotIn("permissionDecision", hook({"command": "npm test"})["hookSpecificOutput"])

    def test_other_tools_are_ignored(self):
        self.assertIsNone(hooks.hook_bash({"tool_name": "Read", "tool_input": {"command": "npm test"}}))

    def test_default_has_hook_permission_off(self):
        self.assertIsNone(config.DEFAULTS["hook_permission"])


class Process(unittest.TestCase):
    """`toknix hook bash` as Claude Code runs it: JSON on stdin, JSON (or nothing) on stdout."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith("TOKNIX_")}
        self.env = dict(env, PYTHONPATH=str(support.SRC), TOKNIX_HOME=str(self.tmp / ".toknix"),
                        HOME=str(self.tmp), USERPROFILE=str(self.tmp), PYTHONDONTWRITEBYTECODE="1")

    def hook_process(self, stdin: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "toknix", "hook", "bash"], input=stdin.encode("utf-8"),
                              capture_output=True, env=dict(self.env, **env), timeout=30)


class TestHookProcess(Process):
    def test_wraps(self):
        p = self.hook_process(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}))
        self.assertEqual((p.returncode, p.stderr), (0, b""))
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["command"],
                         "toknix run -- 'git status'")

    def test_not_wrapped_prints_nothing(self):
        p = self.hook_process(json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}))
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))

    def test_bad_input_is_silent_exit_0(self):
        for stdin in ("", "not json", "[1, 2]", '{"tool_input": "npm test"}', '{"tool_input": {"command": 5}}',
                      '{"tool_input": {"command": "npm test"', "\xff\xfe"):
            with self.subTest(stdin=stdin):
                p = self.hook_process(stdin)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))

    def test_non_ascii_command_survives_any_console_encoding(self):  # stdin is UTF-8 JSON, not the locale
        cmd = "git commit -m 'sửa lỗi giỏ hàng ✓'"
        p = self.hook_process(json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}, ensure_ascii=False),
                              PYTHONIOENCODING="latin-1")
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["command"],
                         "toknix run -- " + shlex.quote(cmd))

    def test_debug_explains_on_stderr_but_still_exits_0(self):
        p = self.hook_process("not json", TOKNIX_DEBUG="1")
        self.assertEqual((p.returncode, p.stdout), (0, b""))
        self.assertIn(b"toknix hook bash:", p.stderr)

    def test_unknown_hook_is_silent(self):
        p = subprocess.run([sys.executable, "-m", "toknix", "hook", "nope"], input=b"{}", capture_output=True,
                           env=self.env, timeout=30)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))


class TestWrappedCommandBehavesTheSame(Process):
    """The rewritten command, run by bash like Claude Code does, gives the original's output and exit code."""

    COMMANDS = [
        "ls tests/fixtures_py | sort",
        "ls 'tests/fixtures_py' && echo 'single $HOME quoted'",
        'ls "tests/fixtures_py" >/dev/null && echo "double $HOME quoted"',
        "ls tests >/dev/null && echo \"it's $TOKNIX_TEST_VAR\" 'say \"hi\"'",
        "ls tests/fixtures | grep -c '\\.txt$' && echo done",
        "find tests/fixtures_py -name '*.py' | sed 's/^/> /'",
        "git --version | cut -c1-11 && echo \"tab:\t|\" 'back\\slash'",
        "ls /definitely/not/here; echo \"rc=$?\"",
        "ls /definitely/not/here",
        "ls tests/fixtures_py && false || echo 'or-branch ran'",
    ]

    def setUp(self):
        super().setUp()
        bin_dir = self.tmp / "bin"  # `toknix` on PATH, as after install
        bin_dir.mkdir()
        shim = bin_dir / "toknix"
        shim.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -m toknix \"$@\"\n")
        shim.chmod(0o755)
        self.env = dict(self.env, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", TOKNIX_TEST_VAR="v a l",
                        LC_ALL="C")

    def bash(self, cmd: str) -> subprocess.CompletedProcess:
        return subprocess.run(runner.shell_argv(cmd), cwd=support.ROOT, env=dict(self.env, **runner.QUIET_ENV),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)

    def test_same_output_and_exit_code(self):
        for cmd in self.COMMANDS:
            with self.subTest(cmd=cmd):
                p = self.hook_process(json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}))
                wrapped = json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
                self.assertTrue(wrapped.startswith("toknix run -- "))
                original, rewritten = self.bash(cmd), self.bash(wrapped)
                self.assertEqual(rewritten.returncode, original.returncode)
                self.assertEqual(rewritten.stdout, original.stdout)


class TestCliHookInProcess(TempHome):
    def test_reads_stdin_and_never_raises(self):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("{oops")), redirect_stdout(out), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["hook", "bash"]), 0)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
