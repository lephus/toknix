"""P2-T2: `tokopt hook read` and `tokopt hook session-start` (A4, N1.4)."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support

from tokopt import config, hooks


class GuardCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / ".tokopt"
        self.proj = self.tmp / "proj"
        env = {k: v for k, v in os.environ.items() if not k.startswith("TOKOPT_")}
        patcher = mock.patch.dict(os.environ, dict(env, TOKOPT_HOME=str(self.home)), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def file(self, rel: str, lines: int = 10, width: int = 50) -> Path:
        p = self.proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(f"{i:05d} " + "x" * (width - 7) + "\n" for i in range(lines)))
        return p

    def config(self, **values):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.json").write_text(json.dumps(values))

    def call(self, tool: str, session: str = "s1", **tool_input) -> dict | None:
        return hooks.hook_read({"session_id": session, "cwd": str(self.proj), "hook_event_name": "PreToolUse",
                                "tool_name": tool, "tool_input": tool_input})

    def read(self, path, session: str = "s1", **span) -> dict | None:
        return self.call("Read", session, file_path=str(path), **span)

    def assertDenied(self, reply: dict | None, *in_reason: str):
        self.assertIsNotNone(reply)
        out = reply["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"))
        for text in in_reason:
            self.assertIn(text, out["permissionDecisionReason"])

    def guard_rows(self) -> list[tuple]:
        if not (self.home / "tokopt.db").exists():
            return []
        con = sqlite3.connect(str(self.home / "tokopt.db"))
        try:
            return con.execute("SELECT session, rule, path, est_tokens FROM guard ORDER BY ts").fetchall()
        finally:
            con.close()


class TestJunk(GuardCase):
    """R1."""

    def test_vendored_and_lockfiles_denied_source_allowed(self):
        nm = self.file("node_modules/x/index.js", lines=100)
        lock = self.file("package-lock.json", lines=100)
        self.assertDenied(self.read(nm), "/node_modules/", f"~{nm.stat().st_size // 4:,} tokens",
                          "Read the source or manifest", "offset/limit")
        self.assertDenied(self.read(lock), "package-lock.json")
        self.assertIsNone(self.read(self.file("src/app.ts")))
        self.assertEqual(self.guard_rows(), [("s1", "junk", str(nm), nm.stat().st_size // 4),
                                             ("s1", "junk", str(lock), lock.stat().st_size // 4)])

    def test_slice_of_a_junk_file_is_allowed(self):  # user decision 2026-10-19 (A4 R1)
        nm = self.file("node_modules/x/index.js", lines=100)
        self.assertIsNone(self.read(nm, offset=10, limit=40))
        self.assertIsNone(self.read(nm, limit=40))
        self.assertIsNone(self.read(nm, offset=10))
        self.assertEqual(self.guard_rows(), [])

    def test_filenames_match_whole_names_suffixes_match_ends(self):
        self.assertIsNone(self.read(self.file("src/my-package-lock.json")))
        self.assertIsNone(self.read(self.file("src/roadmap.ts")))
        self.assertDenied(self.read(self.file("public/app.min.js")), ".min.js")
        self.assertDenied(self.read(self.file("dist2/app.js.map")), ".map")
        self.assertDenied(self.read(self.file("web/yarn.lock")), "yarn.lock")

    def test_windows_paths(self):
        self.assertDenied(self.read(r"C:\work\shop\node_modules\react\index.js"), "/node_modules/")
        self.assertDenied(self.read(r"C:\work\shop\package-lock.json"), "package-lock.json")

    def test_guard_junk_off(self):
        self.config(guard_junk=False)
        self.assertIsNone(self.read(self.file("node_modules/x/index.js")))

    def test_grep_and_glob_in_junk_dirs_are_denied(self):
        self.assertDenied(self.call("Grep", pattern="useState", path=str(self.proj / "node_modules")),
                          "node_modules", "Search source directories")
        self.assertDenied(self.call("Glob", pattern="**/*.js", path="node_modules/react"), "node_modules")
        self.assertIsNone(self.call("Glob", pattern="**/*.js", path=str(self.proj / "src")))
        self.assertIsNone(self.call("Grep", pattern="useState"))  # no path: the project root
        self.assertEqual([r[1:] for r in self.guard_rows()],
                         [("junk", str(self.proj / "node_modules"), 0), ("junk", "node_modules/react", 0)])


class TestBigFile(GuardCase):
    """R2."""

    def test_whole_read_of_a_big_file_is_denied_with_outline_hint(self):
        big = self.file("src/big_module.py", lines=2000)
        self.assertDenied(self.read(big), "2,000 lines", f"~{big.stat().st_size // 4:,} tokens",
                          f"tokopt outline {big}", "offset/limit")
        self.assertEqual(self.guard_rows(), [("s1", "bigfile", str(big), big.stat().st_size // 4)])

    def test_a_range_of_the_same_file_is_allowed(self):
        big = self.file("src/big_module.py", lines=2000)
        self.assertIsNone(self.read(big, limit=100))
        self.assertIsNone(self.read(big, offset=1500, limit=100))
        self.assertEqual(self.guard_rows(), [])

    def test_threshold_is_lines_and_bytes(self):
        self.assertIsNone(self.read(self.file("src/medium.py", lines=799)))
        self.assertIsNone(self.read(self.file("src/short_lines.py", lines=2000, width=10)))  # ≤ 800 × 40 bytes
        self.assertDenied(self.read(self.file("src/edge.py", lines=801)), "801 lines")

    def test_config(self):
        self.config(guard_big_file_lines=3000)
        self.assertIsNone(self.read(self.file("src/big_module.py", lines=2000)))
        self.config(guard_big_file_lines=0)
        self.assertIsNone(self.read(self.file("src/huge.py", lines=5000)))

    def test_quotes_paths_with_spaces(self):
        big = self.file("src/my module.py", lines=2000)
        self.assertDenied(self.read(big), f"tokopt outline '{big}'")


class TestRepeatReads(GuardCase):
    """R3 and the session-start reset."""

    def test_third_identical_read_is_denied(self):
        f = self.file("src/app.ts")
        self.assertIsNone(self.read(f))
        self.assertIsNone(self.read(f))
        self.assertDenied(self.read(f), "already read this exact range", "2 times", "still in your context")
        self.assertEqual([r[1] for r in self.guard_rows()], ["reread"])

    def test_ranges_are_counted_separately(self):
        f = self.file("src/app.ts")
        for _ in range(2):
            self.assertIsNone(self.read(f, offset=1, limit=5))
            self.assertIsNone(self.read(f, offset=6, limit=5))
            self.assertIsNone(self.read(f))
        self.assertDenied(self.read(f, offset=1, limit=5))

    def test_changed_file_can_be_read_again(self):
        f = self.file("src/app.ts")
        for _ in range(3):
            self.read(f)
        st = f.stat()
        os.utime(f, (st.st_atime + 10, st.st_mtime + 10))  # touch
        self.assertIsNone(self.read(f))
        self.assertIsNone(self.read(f))
        self.assertDenied(self.read(f))

    def test_compact_and_clear_reset_the_ledger(self):
        f = self.file("src/app.ts")
        for source in ("compact", "clear"):
            with self.subTest(source=source):
                for _ in range(3):
                    self.read(f)
                self.assertIsNone(hooks.hook_session_start({"session_id": "s1", "source": source}))
                self.assertIsNone(self.read(f))

    def test_startup_and_resume_do_not_reset(self):
        f = self.file("src/app.ts")
        for _ in range(3):
            self.read(f)
        for source in ("startup", "resume"):
            hooks.hook_session_start({"session_id": "s1", "source": source})
        self.assertDenied(self.read(f))

    def test_reset_is_per_session(self):
        f = self.file("src/app.ts")
        for _ in range(3):
            self.read(f, session="other")
        hooks.hook_session_start({"session_id": "s1", "source": "compact"})
        self.assertDenied(self.read(f, session="other"))

    def test_sessions_are_independent(self):
        f = self.file("src/app.ts")
        for _ in range(2):
            self.read(f, session="a")
        self.assertIsNone(self.read(f, session="b"))

    def test_relative_paths_resolve_against_the_hook_cwd(self):
        f = self.file("src/app.ts")
        self.read(f)
        self.read("src/app.ts")
        self.assertDenied(self.read(f))

    def test_off_by_config_or_without_session(self):
        f = self.file("src/app.ts")
        for _ in range(3):
            self.assertIsNone(self.read(f, session=""))
        self.config(guard_repeat_reads=0)
        for _ in range(3):
            self.assertIsNone(self.read(f))

    def test_missing_files_and_directories_are_not_guarded(self):
        for _ in range(3):
            self.assertIsNone(self.read(self.proj / "src" / "nope.ts"))
        (self.proj / "src").mkdir(parents=True)
        for _ in range(3):
            self.assertIsNone(self.read(self.proj / "src"))
        self.assertEqual(self.guard_rows(), [])


class TestGrepHeadLimit(GuardCase):
    """N1.4."""

    def test_content_mode_without_head_limit_gets_200(self):
        reply = self.call("Grep", pattern="TODO", output_mode="content", glob="*.py", **{"-n": True})
        self.assertEqual(reply, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {
            "pattern": "TODO", "output_mode": "content", "glob": "*.py", "-n": True, "head_limit": 200}}})
        self.assertEqual(self.guard_rows(), [])

    def test_left_alone(self):
        self.assertIsNone(self.call("Grep", pattern="TODO", output_mode="content", head_limit=50))
        self.assertIsNone(self.call("Grep", pattern="TODO", output_mode="content", head_limit=0))
        self.assertIsNone(self.call("Grep", pattern="TODO", output_mode="files_with_matches"))
        self.assertIsNone(self.call("Grep", pattern="TODO", output_mode="count"))
        self.assertIsNone(self.call("Grep", pattern="TODO"))  # Claude Code's default is files_with_matches

    def test_config(self):
        self.config(grep_head_limit=0)
        self.assertIsNone(self.call("Grep", pattern="TODO", output_mode="content"))
        self.config(grep_head_limit=500)
        self.assertEqual(self.call("Grep", pattern="TODO", output_mode="content")
                         ["hookSpecificOutput"]["updatedInput"]["head_limit"], 500)

    def test_default(self):
        self.assertEqual(config.DEFAULTS["grep_head_limit"], 200)

    def test_junk_check_comes_first(self):
        self.assertDenied(self.call("Grep", pattern="x", output_mode="content", path="vendor/lib"), "vendor")


class TestOtherTools(GuardCase):
    def test_ignored(self):
        self.assertIsNone(self.call("Bash", command="cat package-lock.json"))
        self.assertIsNone(self.call("Edit", file_path=str(self.file("package-lock.json"))))


class TestReadHookProcess(GuardCase):
    def hook(self, name: str, payload) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()}
        env.update(PYTHONPATH=str(support.SRC), PYTHONDONTWRITEBYTECODE="1")
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, "-m", "tokopt", "hook", name], input=stdin.encode("utf-8"),
                              capture_output=True, env=env, timeout=30)

    def test_deny_reply_and_session_start(self):
        f = self.file("src/app.ts")
        msg = {"session_id": "s9", "cwd": str(self.proj), "tool_name": "Read", "tool_input": {"file_path": str(f)}}
        replies = [self.hook("read", msg) for _ in range(3)]
        self.assertEqual([r.stdout for r in replies[:2]], [b"", b""])
        self.assertEqual(json.loads(replies[2].stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        reset = self.hook("session-start", {"session_id": "s9", "source": "compact", "hook_event_name": "SessionStart"})
        self.assertEqual((reset.returncode, reset.stdout, reset.stderr), (0, b"", b""))
        self.assertEqual(self.hook("read", msg).stdout, b"")

    def test_bad_input_is_silent_exit_0(self):
        for name in ("read", "session-start"):
            for stdin in ("", "nope", "[]", '{"tool_name": "Read", "tool_input": {"file_path": 3}}',
                          '{"session_id": 5, "source": "compact"}'):
                with self.subTest(hook=name, stdin=stdin):
                    p = self.hook(name, stdin)
                    self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))

    def test_unwritable_home_never_blocks_a_read(self):
        broken = self.tmp / "file-not-dir"
        broken.write_text("x")
        with mock.patch.dict(os.environ, {"TOKOPT_HOME": str(broken)}):
            p = self.hook("read", {"session_id": "s", "tool_name": "Read",
                                   "tool_input": {"file_path": str(self.file("src/app.ts"))}})
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))


if __name__ == "__main__":
    unittest.main()
