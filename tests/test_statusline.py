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
from transcript_factory import ClaudeTranscript, subagent_path, text, tool_use

ANSI = re.compile(r"\x1b\[[0-9;]*m")
W = [1.0, 1.25, 0.05, 5.0]  # claude-opus-5-5 in ETU (A1, P8-T2): input, 5-minute cache write, cache read, output


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
                         f"ctx 92k/200k ▮▮▮▮▯▯▯▯▯▯ · {statusline.fmt_num(expected_etu)} ETU · saved ~0 ETU (0.0%) · /clear at 120k\n")  # A7

    def saved_at(self, ts: float, chars: int = 0, guard: int = 0):
        with mock.patch.object(store.time, "time", return_value=ts):
            if chars:
                store.record_run("pytest", "pytest", 1, 1, 1, 1, 1, "", 0, {"default": chars, "hex": 0, "non_ascii": 0})
            if guard:
                store.record_guard("s1", "junk", "x", guard)

    def test_saved_is_etu_of_the_requests_that_reread_it(self):  # P7-T1
        t0 = time.time() - 100
        t = ClaudeTranscript(t0=t0)
        t.assistant([text("a")], cache_read=1000, output=10)  # t0+1
        for _ in range(3):  # t0+2 … t0+4: the first one writes the saving to the cache, two more read it
            t.assistant([text("b")], cache_read=1000, output=10)
        t.write(self.path)
        self.saved_at(t0 - 10_000, chars=40000)  # before the session
        self.saved_at(t0 + 1.5, chars=336000, guard=2000)  # 84,000 + 2,000 tokens
        with mock.patch.object(store.time, "time", return_value=t0 + 1.5):
            store.record_guard("other", "junk", "y", 99999)
        saved = 86000 * (W[1] + W[2] * 2)
        etu = 4 * (10 + 1000 * W[2] + 50)
        self.assertIn(f"saved ~{statusline.fmt_num(saved)} ETU ({saved / (etu + saved):.1%})", self.line())

    def test_saving_after_the_last_request_is_zero_so_far(self):
        t0 = time.time() - 100
        t = ClaudeTranscript(t0=t0)
        t.assistant([text("a")], cache_read=1000, output=10)
        t.write(self.path)
        self.saved_at(t0 + 5, chars=336000)
        self.assertIn("saved ~0 ETU", self.line())

    def test_compaction_stops_the_rereads(self):
        t0 = time.time() - 100
        t = ClaudeTranscript(t0=t0)
        t.assistant([text("a")], cache_read=1000, output=10)  # t0+1
        t.assistant([text("b")], cache_read=1000, output=10)  # t0+2 carries the saving
        t.compact()                                            # t0+3
        t.assistant([text("c")], cache_read=1000, output=10)
        t.assistant([text("d")], cache_read=1000, output=10)
        t.write(self.path)
        self.saved_at(t0 + 1.5, chars=4000)  # 1,000 tokens
        self.assertIn(f"saved ~{statusline.fmt_num(1000 * 1.25)} ETU", self.line())

    def test_old_cache_without_request_times_is_rebuilt(self):
        t0 = time.time() - 100
        t = ClaudeTranscript(t0=t0)
        t.assistant([text("a")], cache_read=1000, output=10)
        t.assistant([text("b")], cache_read=1000, output=10)
        t.write(self.path)
        self.saved_at(t0 + 1.5, chars=4000)
        (self.home / ".toknix" / "sl-s1.json").write_text(json.dumps(
            {"path": str(self.path), "offset": self.path.stat().st_size, "etu": 999.0, "ctx": 2000,
             "start_ts": t0 + 1, "keys": []}))
        line = self.line()
        self.assertIn(f"saved ~{statusline.fmt_num(1000 * 1.25)} ETU", line)
        self.assertIn(f"· {statusline.fmt_num(2 * (10 + 1000 * W[2] + 50))} ETU ·", line)

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

    def ctx_line(self, ctx: int, model: dict | None = None, **config) -> str:
        if config:
            (self.home / ".toknix").mkdir(exist_ok=True)
            (self.home / ".toknix" / "config.json").write_text(json.dumps(config))
        for f in (self.home / ".toknix").glob("sl-*.json"):
            f.unlink()
        t = ClaudeTranscript()
        t.assistant([text("a")], input=0, cache_read=ctx, output=1)
        t.write(self.path)
        return self.line(self.payload(model=model) if model else None)

    def test_budget_not_window_on_one_million_models(self):  # P7-T2: a 1M window never reached 60 %
        line = self.ctx_line(300000, {"id": "claude-opus-5-5[1m]", "display_name": "Opus"})
        self.assertTrue(line.startswith("ctx 300k/200k ▮▮▮▮▮▮▮▮▮▮ "), line)
        self.assertTrue(line.endswith(" · /clear or /compact now\n"), line)

    def test_context_window_smaller_than_budget(self):
        self.assertTrue(self.ctx_line(50000, context_window=100000).startswith("ctx 50k/100k ▮▮▮▮▮▯▯▯▯▯ "))

    def test_bigger_budget_follows_the_window(self):  # a budget above 200k needs a window that holds it
        for model in ({"id": "claude-opus-5-5[1m]", "display_name": "Opus"},
                      {"id": "claude-opus-5-5", "display_name": "Opus (1M context)"},
                      {"id": "claude-opus-5-5", "display_name": "Opus"}):  # ctx above 200k: it must be 1M
            with self.subTest(model=model):
                line = self.ctx_line(300000, model, context_budget=1000000)
                self.assertTrue(line.startswith("ctx 300k/1.0M ▮▮▮▯▯▯▯▯▯▯ "), line)
                self.assertTrue(line.endswith(" · /clear at 600k\n"), line)

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
        self.assertTrue(self.line().startswith("ctx 2.0k/200k ▯"))
        self.assertIn(f"{statusline.fmt_num(10 + 1990 * W[2] + 5)} ETU", self.line())


class TestSubagentFiles(Home):  # P8-T1
    def test_subagent_files_add_to_etu_not_to_ctx(self):
        t = ClaudeTranscript()
        t.assistant([text("a")], input=0, cache_read=92000, cache_write=0, output=0)
        t.write(self.path)
        sub = ClaudeTranscript(agent="a1")
        sub.assistant([text("b")], input=0, cache_read=500000, cache_write=0, output=100)
        sub.write(subagent_path(self.path, "a1"))
        line = self.line()
        self.assertTrue(line.startswith("ctx 92k/200k"), line)
        self.assertIn(f"· {statusline.fmt_num((92000 + 500000) * W[2] + 500)} ETU ·", line)

    def test_appends_and_streamed_usage_are_counted_once(self):
        t = ClaudeTranscript()
        t.assistant([text("a")], input=0, cache_read=1000, output=0)
        t.write(self.path)
        sub = ClaudeTranscript(agent="a1")
        sub.assistant([text("b"), tool_use("x", "Bash", command="ls")], input=0, cache_read=1000, output=4000,
                      partial_output=8)
        last = sub.lines.pop()  # the line with the full output arrives later
        path = subagent_path(self.path, "a1")
        sub.write(path)
        self.assertIn(f"· {statusline.fmt_num(2000 * W[2] + 40)} ETU ·", self.line())
        sub.lines.append(last)
        sub.write(path)
        full = 2000 * W[2] + 20000
        self.assertIn(f"· {statusline.fmt_num(full)} ETU ·", self.line())
        self.assertIn(f"· {statusline.fmt_num(full)} ETU ·", self.line())  # read once
        for f in (self.home / ".toknix").glob("sl-*.json"):
            f.unlink()
        self.assertIn(f"· {statusline.fmt_num(full)} ETU ·", self.line())  # same from scratch

    def test_cache_without_subagents_is_rebuilt(self):
        t = ClaudeTranscript()
        t.assistant([text("a")], input=0, cache_read=1000, output=0)
        t.write(self.path)
        self.line()
        cache = next((self.home / ".toknix").glob("sl-*.json"))
        state = json.loads(cache.read_text())
        state.pop("subs")
        cache.write_text(json.dumps(state))
        sub = ClaudeTranscript(agent="a1")
        sub.assistant([text("b")], input=0, cache_read=10000, output=0)
        sub.write(subagent_path(self.path, "a1"))
        self.assertIn(f"· {statusline.fmt_num(11000 * W[2])} ETU ·", self.line())


class TestPrices(Home):  # P8-T2: each request at its model's price (A1)
    def test_each_line_at_its_models_price(self):
        t = ClaudeTranscript()
        t.assistant([text("a")], input=0, cache_read=100000, output=0)                         # Opus 5.5: 5,000
        t.assistant([text("b")], input=0, cache_read=100000, output=0, model="claude-sonnet-5-5")  # 2,500
        t.assistant([text("c")], input=0, cache_write=1000, cache_write_1h=1000, output=0)    # 1h write: 2,000
        t.assistant([text("d")], input=0, cache_read=1000, output=1, speed="fast")             # 2 × (50 + 5)
        t.write(self.path)
        sub = ClaudeTranscript(agent="a1", model="gpt-5-codex")                                 # no price: weights
        sub.assistant([text("e")], input=0, cache_read=1000, output=0)                           # 100
        sub.write(subagent_path(self.path, "a1"))
        self.assertIn(f"· {statusline.fmt_num(5000 + 2500 + 2000 + 110 + 100)} ETU ·", self.line())

    def test_saved_at_the_one_hour_write_price(self):
        t0 = time.time() - 100
        t = ClaudeTranscript(t0=t0)
        t.assistant([text("a")], cache_read=1000, cache_write=500, cache_write_1h=500, output=10)  # t0+1
        for _ in range(3):
            t.assistant([text("b")], cache_read=1000, output=10)
        t.write(self.path)
        with mock.patch.object(store.time, "time", return_value=t0 + 1.5):
            store.record_guard("s1", "junk", "x", 1000)
        self.assertIn(f"saved ~{statusline.fmt_num(1000 * (2 + W[2] * 2))} ETU", self.line())

    def test_cache_from_before_prices_is_rebuilt(self):
        t = ClaudeTranscript()
        t.assistant([text("a")], input=0, cache_read=1000, output=0)
        t.write(self.path)
        self.line()
        cache = next((self.home / ".toknix").glob("sl-*.json"))
        state = json.loads(cache.read_text())
        state.pop("v")
        state["etu"] = 999999.0  # counted with the old weights
        cache.write_text(json.dumps(state))
        self.assertIn(f"· {statusline.fmt_num(1000 * W[2])} ETU ·", self.line())


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
