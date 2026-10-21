import argparse
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout

import support  # noqa: F401  (puts src/ on sys.path)

from toknix import __version__, cli

V01_COMMANDS = [
    "run", "recall", "gain", "hook",
    "outline", "symbol", "refs", "map",
    "report", "waste", "context", "snapshot", "bench", "compare",
    "statusline", "ui",
    "install", "uninstall", "init-project", "doctor",
]


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def test_version_is_0_1_0(self):
        self.assertEqual(__version__, "0.2.1")
        code, out, _ = call(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "toknix 0.2.1")

    def test_every_v01_command_is_registered(self):
        sub = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
        self.assertEqual(sorted(sub.choices), sorted(V01_COMMANDS))

    def test_stub_reports_not_implemented(self):
        with mock.patch.object(cli, "COMMANDS", cli.COMMANDS + [("future", "not built yet")]):
            code, out, err = call(["future"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("not implemented", err)

    def test_stub_accepts_extra_arguments(self):
        with mock.patch.object(cli, "COMMANDS", cli.COMMANDS + [("future", "not built yet")]):
            code, _, err = call(["future", "--", "foo"])
        self.assertEqual(code, 2)
        self.assertIn("not implemented", err)

    def test_hook_stub_is_silent_and_exits_0(self):
        # A hook must never break the agent, even before it is implemented.
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            code, out, err = call(["hook", "bash"])
        self.assertEqual((code, out, err), (0, "", ""))

    def test_statusline_stub_is_silent_and_exits_0(self):
        # Installed into settings.json by P2-T3; until P4-T4 it must not show an error in Claude Code.
        payload = json.dumps({"session_id": "s", "transcript_path": "/x.jsonl", "model": {"display_name": "Opus"}})
        env = dict(os.environ, PYTHONPATH=str(support.SRC))
        p = subprocess.run([sys.executable, "-m", "toknix", "statusline"], input=payload.encode("utf-8"),
                           capture_output=True, env=env, timeout=30)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))
        with mock.patch.object(sys, "stdin", io.StringIO(payload)):
            self.assertEqual(call(["statusline"]), (0, "", ""))

    def test_no_command_prints_help(self):
        code, out, _ = call([])
        self.assertEqual(code, 0)
        self.assertIn("usage: toknix", out)

    def test_unknown_command_is_a_usage_error(self):
        code, _, err = call(["nope"])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_python_dash_m_toknix(self):
        env = dict(os.environ, PYTHONPATH=str(support.SRC))
        p = subprocess.run([sys.executable, "-m", "toknix", "--version"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "toknix 0.2.1")


if __name__ == "__main__":
    unittest.main()
