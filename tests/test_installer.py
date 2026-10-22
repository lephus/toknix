"""P2-T3: `toknix install` / `uninstall` / `doctor` for Claude Code (A8)."""
from __future__ import annotations

import copy
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support

from toknix import cli, config, installer, store

EXE = "/opt/toknix/bin/toknix"

USER_SETTINGS = {
    "theme": "dark",
    "permissions": {"allow": ["Bash(npm test:*)"], "deny": ["Read(./.env)"]},
    "env": {"FOO": "1"},
    "hooks": {
        "PreToolUse": [{"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "~/bin/check-edit"}]}],
        "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/bin/log-bash"}]}],
    },
    "enabledPlugins": {"x@y": True},
}

TOKNIX_ENTRIES = {
    ("PreToolUse", "Bash"): f"{EXE} hook bash",
    ("PreToolUse", "Read|Grep|Glob"): f"{EXE} hook read",
    ("SessionStart", "compact|clear"): f"{EXE} hook session-start",
    ("UserPromptSubmit", ""): f"{EXE} hook prompt",  # P7-T2: no matcher
    ("PreToolUse", "Agent|Task"): f"{EXE} hook agent",  # P10-T1
}


def toknix_hooks(settings: dict) -> list[tuple[str, str, str]]:
    return [(event, group.get("matcher", ""), h["command"])
            for event, groups in settings.get("hooks", {}).items() for group in groups
            for h in group.get("hooks", []) if "toknix" in h.get("command", "")]


class TempHome(unittest.TestCase):
    """HOME in a temp dir; CLAUDE_CONFIG_DIR and TOKNIX_HOME unset, so both default under it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TOKNIX_") and k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        patcher = mock.patch.dict(os.environ, dict(env, HOME=str(self.home), USERPROFILE=str(self.home)), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = self.home / ".claude" / "settings.json"

    def write_settings(self, data) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(data if isinstance(data, str) else json.dumps(data, indent=4))

    def read_settings(self) -> dict:
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TestPathsFollowHome(TempHome):
    def test_config_dirs_are_read_at_call_time(self):  # never the real ~ in tests
        self.assertEqual(config.claude_dir(), self.home / ".claude")
        self.assertEqual(config.toknix_home(), self.home / ".toknix")
        self.assertEqual(config.codex_dir(), self.home / ".codex")
        self.assertEqual(config.gemini_dir(), self.home / ".gemini")

    def test_env_overrides_still_win(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home / "cc")}):
            self.assertEqual(config.claude_dir(), self.home / "cc")


class TestInstall(TempHome):
    def test_keeps_user_settings_and_adds_four_hooks_and_statusline(self):
        self.write_settings(USER_SETTINGS)
        installer.install(exe=EXE)
        s = self.read_settings()
        for key, value in USER_SETTINGS.items():
            if key != "hooks":
                self.assertEqual(s[key], value)
        for event, groups in USER_SETTINGS["hooks"].items():
            self.assertEqual(s["hooks"][event][:len(groups)], groups)  # user's entries first, unchanged
        self.assertEqual(sorted(toknix_hooks(s)), sorted((e, m, c) for (e, m), c in TOKNIX_ENTRIES.items()))
        self.assertEqual(s["statusLine"], {"type": "command", "command": f"{EXE} statusline"})

    def test_hook_entry_shape(self):
        installer.install(exe=EXE)
        self.assertIn({"matcher": "Bash", "hooks": [{"type": "command", "command": f"{EXE} hook bash"}]},
                      self.read_settings()["hooks"]["PreToolUse"])

    def test_existing_statusline_is_kept(self):
        mine = {"type": "command", "command": "~/bin/my-status", "padding": 1}
        self.write_settings({"statusLine": mine})
        messages = installer.install(exe=EXE)
        self.assertEqual(self.read_settings()["statusLine"], mine)
        self.assertTrue(any("statusLine" in m and "kept" in m for m in messages), messages)

    def test_twice_is_idempotent(self):
        self.write_settings(USER_SETTINGS)
        installer.install(exe=EXE)
        once = self.read_settings()
        installer.install(exe=EXE)
        self.assertEqual(self.read_settings(), once)
        self.assertEqual(len(toknix_hooks(once)), len(TOKNIX_ENTRIES))

    def test_reinstall_from_a_new_path_updates_in_place(self):
        installer.install(exe=EXE)
        installer.install(exe="/new/place/toknix")
        s = self.read_settings()
        self.assertEqual(sorted(c for _, _, c in toknix_hooks(s)),
                         ["/new/place/toknix hook agent", "/new/place/toknix hook bash", "/new/place/toknix hook prompt", "/new/place/toknix hook read",
                          "/new/place/toknix hook session-start"])
        self.assertEqual(s["statusLine"]["command"], "/new/place/toknix statusline")

    def test_creates_settings_when_missing(self):
        installer.install(exe=EXE)
        self.assertEqual(len(toknix_hooks(self.read_settings())), len(TOKNIX_ENTRIES))

    def test_backup_of_previous_settings(self):
        self.write_settings(USER_SETTINGS)
        before = self.settings.read_bytes()
        installer.install(exe=EXE)
        backups = list((self.home / ".toknix" / "backups").glob("*/settings.json"))
        self.assertEqual([b.read_bytes() for b in backups], [before])

    def test_invalid_settings_are_never_overwritten(self):
        self.write_settings('{"theme": "dark",}')
        code, out, err = self.main("install")
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)
        self.assertEqual(self.settings.read_text(), '{"theme": "dark",}')

    def test_settings_that_are_not_an_object_are_refused(self):
        self.write_settings("[]")
        self.assertEqual(self.main("install")[0], 1)
        self.assertEqual(self.settings.read_text(), "[]")

    def test_respects_claude_config_dir(self):
        cc = self.home / "elsewhere"
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(cc)}):
            installer.install(exe=EXE)
        self.assertTrue((cc / "settings.json").exists())
        self.assertFalse(self.settings.exists())

    def test_keeps_non_ascii_and_writes_utf8(self):
        self.write_settings({"env": {"GREETING": "xin chào"}})
        installer.install(exe=EXE)
        self.assertIn("xin chào", self.settings.read_text(encoding="utf-8"))


class TestUninstall(TempHome):
    def round_trip(self, original):
        if original is not None:
            self.write_settings(original)
        installer.install(exe=EXE)
        installer.install(exe=EXE)
        installer.uninstall()
        if original is None:
            self.assertFalse(self.settings.exists())
        else:
            self.assertEqual(self.read_settings(), original)

    def test_restores_rich_settings(self):
        self.round_trip(copy.deepcopy(USER_SETTINGS))

    def test_restores_empty_containers_that_existed(self):
        for original in ({}, {"hooks": {}}, {"hooks": {"PreToolUse": []}}, {"hooks": {"SessionStart": []}}):
            with self.subTest(original=original):
                self.round_trip(original)

    def test_removes_the_file_it_created(self):
        self.round_trip(None)

    def test_keeps_user_statusline_and_changes_made_after_install(self):
        self.write_settings({"statusLine": {"type": "command", "command": "mine"}})
        installer.install(exe=EXE)
        s = self.read_settings()
        s["model"] = "opus"
        s["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [{"type": "command", "command": "~/later"}]})
        self.write_settings(s)
        installer.uninstall()
        self.assertEqual(self.read_settings(), {
            "statusLine": {"type": "command", "command": "mine"}, "model": "opus",
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/later"}]}]}})

    def test_without_the_install_record_removes_emptied_containers(self):
        installer.install(exe=EXE)
        (self.home / ".toknix" / "install.json").unlink()
        installer.uninstall()
        self.assertEqual(self.read_settings(), {})

    def test_only_toknix_entries_are_removed_from_a_shared_group(self):
        self.write_settings({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "~/bin/audit"},
            {"type": "command", "command": "'/my dir/toknix' hook bash"}]}]}})
        installer.uninstall()
        self.assertEqual(self.read_settings(), {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "~/bin/audit"}]}]}})

    def test_nothing_installed(self):
        self.write_settings(USER_SETTINGS)
        before = self.settings.read_bytes()
        code, out, _ = self.main("uninstall")
        self.assertEqual(code, 0)
        self.assertIn("nothing to remove", out)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_no_settings_file(self):
        self.assertEqual(self.main("uninstall")[0], 0)
        self.assertFalse(self.settings.exists())


class TestCommandPath(unittest.TestCase):
    def test_console_script(self):
        self.assertEqual(installer.toknix_command(argv0="/home/u/.local/bin/toknix", which=lambda _: None),
                         "/home/u/.local/bin/toknix")

    def test_windows_exe_and_spaces_are_quoted(self):
        self.assertEqual(installer.toknix_command(argv0=r"C:\Users\Thu Ha\venv\Scripts\toknix.exe",
                                                  which=lambda _: None),
                         '"C:/Users/Thu Ha/venv/Scripts/toknix.exe"')  # works in bash and cmd
        self.assertEqual(installer.toknix_command(argv0=r"C:\tools\toknix.exe", which=lambda _: None),
                         "C:/tools/toknix.exe")

    def test_python_dash_m_uses_path_lookup_then_this_python(self):
        argv0 = "/src/toknix/__main__.py"
        self.assertEqual(installer.toknix_command(argv0=argv0, which=lambda _: "/usr/local/bin/toknix"),
                         "/usr/local/bin/toknix")
        self.assertEqual(installer.toknix_command(argv0=argv0, which=lambda _: None, python="/usr/bin/python3"),
                         "/usr/bin/python3 -m toknix")

    def test_install_warns_when_toknix_is_not_on_path(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp, "TOKNIX_HOME": tmp}), \
                mock.patch.object(installer.shutil, "which", return_value=None), \
                mock.patch.object(installer.sys, "argv", ["/src/toknix/__main__.py"]):
            msgs = installer.install()
        self.assertTrue(any("not on PATH" in m and "-m toknix" in m for m in msgs), msgs)

    def test_install_without_exe_uses_it(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp, "TOKNIX_HOME": tmp}), \
                mock.patch.object(installer, "toknix_command", return_value="/x/toknix"):
            installer.install()
            self.assertIn("/x/toknix hook bash", (Path(tmp) / "settings.json").read_text())


class TestLegacyName(TempHome):
    """toknix was published as tokopt for a day: its hooks, statusLine and rules block are taken over."""

    def test_install_replaces_and_uninstall_removes_tokopt_entries(self):
        legacy = {"hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/old/tokopt hook bash"}]},
                           {"matcher": "Read|Grep|Glob", "hooks": [{"type": "command", "command": "/old/tokopt hook read"}]}],
            "SessionStart": [{"matcher": "compact|clear",
                              "hooks": [{"type": "command", "command": "/old/tokopt hook session-start"}]}]},
            "statusLine": {"type": "command", "command": "/old/tokopt statusline"}, "theme": "dark"}
        self.write_settings(legacy)
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.write_text("# mine\n\n<!-- tokopt:begin v1 -->\nold rules\n<!-- tokopt:end -->\n")
        installer.install(exe=EXE)
        s = self.read_settings()
        commands = sorted(c for _, _, c in toknix_hooks(s))
        self.assertEqual(commands, sorted(TOKNIX_ENTRIES.values()))  # replaced in place, no second copy
        self.assertNotIn("tokopt", json.dumps(s))
        self.assertEqual(s["statusLine"]["command"], f"{EXE} statusline")
        text = claude_md.read_text()
        self.assertNotIn("tokopt", text)
        self.assertEqual(text.count("<!-- toknix:begin v1 -->"), 1)
        installer.uninstall()
        # keys that existed before install stay (here as empty lists), as for any other settings file
        self.assertEqual(self.read_settings(), {"hooks": {"PreToolUse": [], "SessionStart": []}, "theme": "dark"})
        self.assertEqual(claude_md.read_text(), "# mine\n")


class TestWindowsPaths(unittest.TestCase):
    """Found by the first Windows CI run: console scripts have no .exe in argv[0], and shlex ate backslashes."""

    def test_exe_suffix_added_when_the_exe_exists(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "toknix.exe").write_text("")
            got = installer.toknix_command(argv0=str(Path(d) / "toknix"), which=lambda _: None)
            self.assertTrue(got.strip('"').replace("\\", "/").endswith("/toknix.exe"), got)
            plain = installer.toknix_command(argv0=str(Path(d) / "other" / "toknix"), which=lambda _: None)
            self.assertFalse(plain.strip('"').endswith(".exe"), plain)

    def test_program_of_a_hook_command(self):
        cases = {r"C:\a\b\toknix hook bash": r"C:\a\b\toknix",
                 '"C:/Program Files/t/toknix.exe" hook read': "C:/Program Files/t/toknix.exe",
                 "/opt/t/toknix hook session-start": "/opt/t/toknix",
                 "'/my dir/toknix' hook bash": "/my dir/toknix",
                 "/usr/bin/python3 -m toknix hook bash": "/usr/bin/python3",
                 '"C:/py 3/python.exe" -m toknix hook read': "C:/py 3/python.exe"}
        for command, program in cases.items():
            with self.subTest(command=command):
                self.assertEqual(installer._program(command), program)

    def test_an_exe_counts_as_present(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "toknix.exe").write_text("")
            self.assertIsNone(installer._missing_program(f"{d}/toknix hook bash"))
            self.assertIsNotNone(installer._missing_program(f"{d}/gone hook bash"))


class TestDoctor(TempHome):
    def test_before_install(self):
        code, out, _ = self.main("doctor")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"hook bash\s+missing")
        self.assertIn("toknix install", out)

    def test_after_install(self):
        exe = installer._quote(str(self.fake_toknix("echo 'toknix 0.1.0'")))  # as toknix_command writes it
        installer.install(exe=exe)
        with mock.patch.object(store.time, "time", return_value=1_000.0):  # 10 days before "now"
            store.record_run("pytest", "pytest", 1, 10, 5, 1, 1, "")
        for _ in range(2):
            store.record_run("pytest", "pytest", 1, 10, 5, 1, 1, "")
        store.record_guard("s", "junk", "x", 1)
        store.record_route("s", "general-purpose", "implement", "claude-opus-5-5", "sonnet")  # P10-T1
        with mock.patch("toknix.installer.time.time", return_value=1_000.0 + 10 * 86400):
            code, out, err = self.main("doctor")
        self.assertEqual((code, err), (0, ""))
        for name in ("bash", "read", "session-start", "prompt", "agent"):
            self.assertRegex(out, rf"hook {name}\s+installed: {re.escape(exe)} hook {name}")
        self.assertIn(f"statusLine", out)
        self.assertIn(f"Python {sys.version.split()[0]}", out)
        self.assertIn(sys.executable, out)
        self.assertIn(str(self.settings), out)
        self.assertRegex(out, r"last 7 days: 2 runs, 1 guard events, 1 subagents routed")

    def fake_toknix(self, body: str) -> Path:
        exe = self.home / "bin" / "toknix"
        exe.parent.mkdir(exist_ok=True)
        exe.write_text(f"#!/bin/sh\n{body}\n")
        exe.chmod(0o755)
        return exe

    def test_installed_command_that_does_not_run(self):  # e.g. `python -m toknix` without toknix importable
        installer.install(exe=installer._quote(str(self.fake_toknix("echo 'No module named toknix' >&2; exit 1"))))
        code, out, _ = self.main("doctor")
        self.assertEqual(code, 1)
        self.assertIn("does not run: No module named toknix", out)

    def test_installed_command_that_no_longer_exists(self):
        installer.install(exe=str(self.home / "gone" / "toknix"))
        code, out, _ = self.main("doctor")
        self.assertEqual(code, 1)
        self.assertIn("not found", out)


class TestProcess(TempHome):
    """The real CLI, with HOME pointing at the temp dir: nothing outside it is touched."""

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONPATH=str(support.SRC), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, "-m", "toknix", *args], capture_output=True, env=env, timeout=60)

    def test_round_trip(self):
        self.write_settings(USER_SETTINGS)
        p = self.run_cli("install")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(toknix_hooks(self.read_settings())), len(TOKNIX_ENTRIES))
        self.assertEqual(self.run_cli("doctor").returncode, 0)
        self.assertEqual(self.run_cli("uninstall").returncode, 0)
        self.assertEqual(self.read_settings(), USER_SETTINGS)
        written = sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*") if p.is_file())
        self.assertTrue(all(w.startswith((".claude", ".toknix")) for w in written), written)


if __name__ == "__main__":
    unittest.main()
