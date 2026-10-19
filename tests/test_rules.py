"""P3-T5: rules block v1 and its install into CLAUDE.md / AGENTS.md / GEMINI.md (A8 step 3, N1.5b)."""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from toknix import cli, installer
from toknix.textutil import est_tokens

TEMPLATES = Path(installer.__file__).resolve().parent / "templates"
EXE = "/opt/toknix/bin/toknix"
BLOCK_RE = re.compile(r"<!-- toknix:begin v(\d+) -->\n(.*?)<!-- toknix:end -->\n?", re.S)


class TestTemplates(unittest.TestCase):
    def test_v1_and_variants_fit_350_tokens(self):
        files = [TEMPLATES / "rules.md"] + sorted((TEMPLATES / "variants").glob("*.md"))
        self.assertGreaterEqual(len(files), 2)
        for f in files:
            with self.subTest(template=f.name):
                self.assertLessEqual(est_tokens(f.read_text(encoding="utf-8")), 350)

    def test_v1_says_what_the_plan_asks(self):
        text = (TEMPLATES / "rules.md").read_text(encoding="utf-8")
        for needle in ("toknix outline", "toknix symbol", "toknix refs", "toknix recall",  # tools first
                       "diff", "Do not summarize", "do not paste code that is already in the diff",  # write less
                       "Edit, not by rewriting them with Write", "Answer briefly",
                       "debugging", "security", "migrations",  # keep full reasoning
                       "One task per session"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_packaged(self):  # pip/uv installs must carry the templates
        pyproject = (Path(support.ROOT) / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(pyproject, r'(?s)\[tool\.setuptools\.package-data\].*"templates/\*\.md"')
        self.assertRegex(pyproject, r'(?s)\[tool\.setuptools\.package-data\].*"templates/variants/\*\.md"')


class Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TOKNIX_") and k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        patcher = mock.patch.dict(os.environ, dict(env, HOME=str(self.home), USERPROFILE=str(self.home)), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.claude_md = self.home / ".claude" / "CLAUDE.md"
        self.agents_md = self.home / ".codex" / "AGENTS.md"
        self.gemini_md = self.home / ".gemini" / "GEMINI.md"
        self.v1 = (TEMPLATES / "rules.md").read_text(encoding="utf-8")

    def put(self, path: Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def blocks(self, path: Path) -> list[tuple[str, str]]:
        return BLOCK_RE.findall(path.read_text(encoding="utf-8"))


class TestInstallRules(Home):
    def test_new_claude_md(self):
        installer.install(exe=EXE)
        self.assertEqual(self.claude_md.read_text(encoding="utf-8"),
                         f"<!-- toknix:begin v1 -->\n{self.v1}<!-- toknix:end -->\n")

    def test_keeps_the_rest_of_the_file(self):
        mine = "# My rules\n\n- be nice\n"
        self.put(self.claude_md, mine.encode())
        installer.install(exe=EXE)
        text = self.claude_md.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(mine))
        self.assertEqual(self.blocks(self.claude_md), [("1", self.v1)])

    def test_twice_is_one_block(self):
        self.put(self.claude_md, b"# mine\n")
        installer.install(exe=EXE)
        once = self.claude_md.read_bytes()
        installer.install(exe=EXE)
        self.assertEqual(self.claude_md.read_bytes(), once)

    def test_old_block_is_replaced_in_place(self):
        self.put(self.claude_md, b"# top\n\n<!-- toknix:begin v0 -->\nold rules\n<!-- toknix:end -->\n\n# bottom\n")
        installer.install(exe=EXE)
        text = self.claude_md.read_text(encoding="utf-8")
        self.assertEqual(self.blocks(self.claude_md), [("1", self.v1)])
        self.assertTrue(text.startswith("# top\n\n<!-- toknix:begin v1 -->\n"))
        self.assertTrue(text.endswith("<!-- toknix:end -->\n\n# bottom\n"))

    def test_codex_and_gemini_only_when_installed(self):
        installer.install(exe=EXE)
        self.assertFalse((self.home / ".codex").exists())
        self.assertFalse((self.home / ".gemini").exists())
        (self.home / ".codex").mkdir()
        (self.home / ".gemini").mkdir()
        installer.install(exe=EXE)
        self.assertEqual(self.blocks(self.agents_md), [("1", self.v1)])
        self.assertEqual(self.blocks(self.gemini_md), [("1", self.v1)])

    def test_codex_home_is_honoured(self):
        codex = self.home / "elsewhere-codex"
        codex.mkdir()
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex)}):
            installer.install(exe=EXE)
        self.assertEqual(self.blocks(codex / "AGENTS.md"), [("1", self.v1)])

    def test_messages(self):
        msgs = installer.install(exe=EXE)
        self.assertIn(f"added rules v1 → {self.claude_md}", msgs)
        msgs = installer.install(exe=EXE)
        self.assertIn(f"rules v1 already in {self.claude_md}", msgs)


class TestUninstallRules(Home):
    def round_trip(self, path: Path, original: bytes | None):
        if original is not None:
            self.put(path, original)
        installer.install(exe=EXE)
        installer.install(exe=EXE)
        installer.uninstall()
        if original is None:
            self.assertFalse(path.exists())
        else:
            self.assertEqual(path.read_bytes(), original)

    def test_byte_exact(self):
        cases = [b"", b"# mine\n", b"# mine", b"# mine\n\n", b"# a\r\n- b\r\n", "# tiếng Việt ✓\n".encode()]
        for original in cases:
            with self.subTest(original=original):
                self.round_trip(self.claude_md, original)

    def test_created_file_is_removed(self):
        self.round_trip(self.claude_md, None)

    def test_agents_and_gemini(self):
        (self.home / ".codex").mkdir()
        self.round_trip(self.agents_md, None)
        (self.home / ".gemini").mkdir()
        self.round_trip(self.gemini_md, b"# gemini rules\n")

    def test_without_the_install_record(self):
        self.put(self.claude_md, b"# mine\n")
        installer.install(exe=EXE)
        (self.home / ".toknix" / "install.json").unlink()
        installer.uninstall()
        self.assertEqual(self.claude_md.read_bytes(), b"# mine\n")

    def test_user_edits_around_the_block_are_kept(self):
        self.put(self.claude_md, b"# mine\n")
        installer.install(exe=EXE)
        self.claude_md.write_bytes(self.claude_md.read_bytes() + b"\n# added later\n")
        installer.uninstall()
        self.assertEqual(self.claude_md.read_bytes(), b"# mine\n\n# added later\n")


class TestDoctorRules(Home):
    def doctor(self) -> str:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            cli.main(["doctor"])
        return out.getvalue()

    def test_lists_each_file(self):
        (self.home / ".gemini").mkdir()
        installer.install(exe=EXE)
        out = self.doctor()
        self.assertRegex(out, rf"rules\s+v1 in {re.escape(str(self.claude_md))}")
        self.assertRegex(out, rf"rules\s+v1 in {re.escape(str(self.gemini_md))}")
        self.assertRegex(out, r"rules\s+Codex not installed")

    def test_missing(self):
        out = self.doctor()
        self.assertRegex(out, rf"rules\s+missing in {re.escape(str(self.claude_md))}")


if __name__ == "__main__":
    unittest.main()
