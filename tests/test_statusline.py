"""P4-T4: `toknix statusline` (A7)."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import support

from toknix import statusline, store
from transcript_factory import ClaudeTranscript, text, tool_use

ANSI = re.compile(r"\x1b\[[0-9;]*m")
W = [1.0, 1.25, 0.1, 5.0]


class Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TOKNIX_") and k not in ("CLAUDE_CONFIG_DIR", "NO_COLOR")}
        patcher = mock.patch.dict(os.environ, dict(env, HOME=str(self.home), USERPROFILE=str(self.home)), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = self.home / ".claude" / "projects" / "-w" / "s1.jsonl"

    def payload(self, **extra) -> str:
        return json.dumps({"session_id": "s1", "transcript_path": str(self.path), "cwd": "/w",
                           "model": {"id": "claude-opus-5-5", "display_name": "Opus"}, **extra})

    def line(self, payload: str | None = None, plain: bool = True) -> str:
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(payload if payload is not None else self.payload())), \
                redirect_stdout(out):
            self.assertEqual(statusline.main(), 0)
        return ANSI.sub("", out.getvalue()) if plain else out.getvalue()


class TestLine(Home):
    def test_context_bar_etu_and_hint(self):
        t = ClaudeTranscript()
        t.assistant([text("a")], input=10, cache_read=50000, cache_write=10000, output=200)
        t.assistant([text("b")], input=20, cache_read=60000, cache_write=31980, output=300)  # ctx 92,000 = 46 %
        t.write(self.path)
        expected_etu = sum(W[0] * i + W[1] * cw + W[2] * cr + W[3] * o
                           for i, cr, cw, o in ((10, 50000, 10000, 200), (20, 60000, 31980, 300)))
        self.assertEqual(self.line(),
                         f"ctx 46% ▮▮▮▮▯▯▯▯▯▯ · {statusline.fmt_num(expected_etu)} ETU · saved ~0 · /clear at 60%\n")  # A7

    def test_saved_counts_this_session_only(self):
        t = ClaudeTranscript(t0=time.time() - 100)
        t.assistant([text("a")], cache_read=1000, output=10)
        t.write(self.path)
        with mock.patch.object(store.time, "time", return_value=time.time() - 10_000):  # before the session
            store.record_run("pytest", "pytest", 1, 1, 1, 1, 1, "", 0, {"default": 40000, "hex": 0, "non_ascii": 0})
        store.record_run("pytest", "pytest", 1, 1, 1, 1, 1, "", 0, {"default": 336000, "hex": 0, "non_ascii": 0})
        store.record_guard("s1", "junk", "x", 2000)
        store.record_guard("other", "junk", "y", 99999)
        self.assertIn("saved ~86.0k", self.line())  # 84,000 from the run + 2,000 from this session's guard

    def test_colours(self):
        for pct, colour in ((59, None), (60, "33"), (79, "33"), (80, "31"), (97, "31")):
            with self.subTest(pct=pct):
                t = ClaudeTranscript()
                t.assistant([text("a")], input=0, cache_read=pct * 2000, cache_write=0, output=1)
                t.write(self.path)
                for f in (self.home / ".toknix").glob("sl-*.json"):
                    f.unlink()
                raw = self.line(plain=False)
                codes = re.findall(r"\x1b\[(\d+)m", raw)
                self.assertEqual(codes[:1], [colour] if colour else [])

    def test_no_color(self):
        t = ClaudeTranscript()
        t.assistant([text("a")], cache_read=190000, output=1)
        t.write(self.path)
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertNotIn("\x1b[", self.line(plain=False))

    def test_context_window_from_config(self):
        (self.home / ".toknix").mkdir()
        (self.home / ".toknix" / "config.json").write_text('{"context_window": 1000000}')
        t = ClaudeTranscript()
        t.assistant([text("a")], cache_read=100000, output=1)
        t.write(self.path)
        self.assertTrue(self.line().startswith("ctx 10% "))

    def test_one_million_context_models(self):  # a real session showed "ctx 467%" with the 200k default
        t = ClaudeTranscript()
        t.assistant([text("a")], cache_read=300000, output=1)
        t.write(self.path)
        for model in ({"id": "claude-opus-5-5[1m]", "display_name": "Opus"},
                      {"id": "claude-opus-5-5", "display_name": "Opus (1M context)"},
                      {"id": "claude-opus-5-5", "display_name": "Opus"}):  # ctx above 200k: it must be 1M
            with self.subTest(model=model):
                self.assertTrue(self.line(self.payload(model=model)).startswith("ctx 30% "))

    def test_bad_input_never_errors(self):
        for payload in ("", "nope", "[]", json.dumps({"transcript_path": str(self.home / "missing.jsonl")}),
                        json.dumps({"session_id": "x"})):
            with self.subTest(payload=payload):
                out = io.StringIO()
                with mock.patch.object(sys, "stdin", io.StringIO(payload)), redirect_stdout(out):
                    self.assertEqual(statusline.main(), 0)
                self.assertNotIn("Traceback", out.getvalue())


class TestIncremental(Home):
    def test_appends_are_read_once(self):
        t = ClaudeTranscript()
        t.assistant([text("a")], cache_read=1000, output=10)
        t.write(self.path)
        first = self.line()
        t.assistant([text("b")], cache_read=3000, output=20)
        t.write(self.path)
        second = self.line()
        cache = json.loads(next((self.home / ".toknix").glob("sl-*.json")).read_text())
        self.assertEqual(cache["offset"], self.path.stat().st_size)
        for f in (self.home / ".toknix").glob("sl-*.json"):
            f.unlink()
        self.assertEqual(self.line(), second)
        self.assertNotEqual(first, second)

    def test_one_request_split_across_two_reads_counts_once(self):
        t = ClaudeTranscript()
        t.assistant([text("a"), tool_use("x", "Bash", command="ls")], cache_read=1000, output=100, repeat_usage=False)
        t.write(self.path)
        once = self.line()
        t.lines.append(t.lines[-1])  # the request's second line, with the same usage, arrives later
        t.write(self.path)
        self.assertEqual(self.line(), once)

    def test_half_written_last_line_waits(self):
        t = ClaudeTranscript()
        t.assistant([text("a")], cache_read=1000, output=10)
        t.write(self.path)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"type": "assistant", "requestId": "req_9", "message": {"id": "m9", "usage": {"output_tok')
        before = self.line()
        cache = json.loads(next((self.home / ".toknix").glob("sl-*.json")).read_text())
        self.assertLess(cache["offset"], self.path.stat().st_size)  # not consumed yet
        self.assertIn("ctx", before)

    def test_rewritten_file_starts_over(self):
        t = ClaudeTranscript()
        for _ in range(5):
            t.assistant([text("a")], cache_read=100000, output=1000)
        t.write(self.path)
        self.line()
        small = ClaudeTranscript()
        small.assistant([text("b")], cache_read=1990, output=1)  # ctx 2,000 = 1 %
        small.write(self.path)
        self.assertTrue(self.line().startswith("ctx 1% "))
        self.assertIn(f"{statusline.fmt_num(10 + 1990 * 0.1 + 5)} ETU", self.line())


class TestSpeed(Home):
    """Plan: a 20 MB transcript, first run < 1 s, later runs < 150 ms (incremental read)."""

    def test_twenty_megabytes(self):
        t = ClaudeTranscript()
        body = "x" * 3000
        while sum(len(l) + 1 for l in t.lines) < 20_000_000:
            t.assistant([tool_use(f"b{len(t.lines)}", "Bash", command="npm test")], cache_read=50000, output=50)
            t.user(tool_results=[(f"b{len(t.lines) - 1}", body)])
        t.write(self.path)
        env = dict(os.environ, PYTHONPATH=str(support.SRC))
        slow = 3 if os.environ.get("CI") else 1

        def timed() -> float:
            start = time.perf_counter()
            p = subprocess.run([sys.executable, "-m", "toknix", "statusline"], input=self.payload().encode(),
                               capture_output=True, env=env, timeout=60)
            self.assertIn(b"ctx", p.stdout, p.stderr)
            return time.perf_counter() - start

        self.assertLess(timed(), 1.0 * slow)
        t.assistant([text("one more")], cache_read=60000, output=10)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(t.lines[-1] + "\n")
        self.assertLess(timed(), 0.150 * slow)


if __name__ == "__main__":
    unittest.main()
