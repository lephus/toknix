"""`tokopt init-project` (A8): project deny rules for junk directories and a .geminiignore."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from tokopt import cli, config, installer


class Project(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, {"TOKOPT_HOME": str(self.root / ".tokopt-home"),
                                               "HOME": str(self.root / "home")})
        patcher.start()
        self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.root)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["init-project", *argv])
        return code, out.getvalue(), err.getvalue()

    def settings(self) -> dict:
        return json.loads((self.root / ".claude" / "settings.json").read_text())

    def junk_dirs(self) -> list[str]:
        return [p.strip("/") for p in config.DEFAULTS["junk_patterns"] if p.startswith("/") and p.endswith("/")]


class TestInitProject(Project):
    def test_new_project(self):
        code, out, err = self.run_cli()
        self.assertEqual((code, err), (0, ""))
        deny = self.settings()["permissions"]["deny"]
        self.assertEqual(deny, [f"Read(./**/{d}/**)" for d in self.junk_dirs()])
        ignore = (self.root / ".geminiignore").read_text()
        for d in self.junk_dirs():
            self.assertIn(f"{d}/\n", ignore)
        self.assertIn("added", out)

    def test_keeps_existing_settings_and_is_idempotent(self):
        (self.root / ".claude").mkdir()
        mine = {"permissions": {"deny": ["Read(./.env)"], "allow": ["Bash(npm test:*)"]}, "model": "opus"}
        (self.root / ".claude" / "settings.json").write_text(json.dumps(mine))
        (self.root / ".geminiignore").write_text("secrets/\n")
        self.run_cli()
        once = (self.settings(), (self.root / ".geminiignore").read_text())
        self.run_cli()
        self.assertEqual((self.settings(), (self.root / ".geminiignore").read_text()), once)
        s = self.settings()
        self.assertEqual(s["model"], "opus")
        self.assertEqual(s["permissions"]["allow"], ["Bash(npm test:*)"])
        self.assertEqual(s["permissions"]["deny"][0], "Read(./.env)")
        self.assertEqual(len(s["permissions"]["deny"]), 1 + len(self.junk_dirs()))
        self.assertTrue(once[1].startswith("secrets/\n"))

    def test_path_option_and_invalid_settings(self):
        other = self.root / "other"
        other.mkdir()
        self.assertEqual(self.run_cli("--path", str(other))[0], 0)
        self.assertTrue((other / ".claude" / "settings.json").exists())
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "settings.json").write_text("{oops")
        code, _, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)
        self.assertEqual((self.root / ".claude" / "settings.json").read_text(), "{oops")

    def test_never_touches_the_user_level_settings(self):
        self.run_cli()
        self.assertFalse((self.root / "home" / ".claude").exists())

    def test_function_reports_what_it_did(self):
        msgs = installer.init_project(self.root)
        self.assertTrue(any(".claude/settings.json" in m for m in msgs))
        self.assertTrue(any(".geminiignore" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()
