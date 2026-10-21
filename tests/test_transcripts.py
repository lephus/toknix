"""P4-T1: transcript parsers (A6.1) and char/token calibration (A6.4, N2.2)."""
from __future__ import annotations

import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from toknix import config, filters, measure, transcripts
from transcript_factory import ClaudeTranscript, CodexRollout, subagent_path, text, thinking, tool_use


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

    def claude_file(self, t: ClaudeTranscript, project: str = "-w-repo", name: str | None = None) -> Path:
        return t.write(self.home / ".claude" / "projects" / project / f"{name or t.session}.jsonl")


class TestClaudeParser(Home):
    def test_chunks_of_one_request_count_once(self):
        t = ClaudeTranscript()
        t.user("fix the bug")
        t.assistant([thinking(), text("Let me look."), tool_use("tu1", "Read", file_path="/w/a.py")],
                    input=100, cache_read=2000, cache_write=300, output=40)
        t.user(tool_results=[("tu1", "print('hi')\n")])
        t.assistant([text("Done.")], input=5, cache_read=2300, cache_write=60, output=7)
        s = transcripts.parse_claude(self.claude_file(t))
        self.assertEqual(len(s.requests), 2)
        self.assertEqual(s.totals(), {"input": 105, "cache_write": 360, "cache_read": 4300, "output": 47})
        r = s.requests[0]
        self.assertEqual([u.name for u in r.tool_uses], ["Read"])  # blocks of the 3 lines merged
        self.assertEqual(r.text_chars, len("Let me look."))
        self.assertTrue(r.thinking_hidden)
        self.assertEqual(r.context, 2400)

    def test_broken_lines_are_skipped(self):
        t = ClaudeTranscript()
        t.junk()
        t.assistant([text("a")], output=3)
        t.junk("[1, 2, 3]")
        t.junk('{"type": "assistant", "message": "not a dict"}')
        t.junk('{"type": "assistant", "message": {"id": "x", "usage": {"input_tokens": "lots"}}}')
        t.junk("")
        t.assistant([text("b")], output=4)
        s = transcripts.parse_claude(self.claude_file(t))
        self.assertEqual([r.output for r in s.requests], [3, 4])

    def test_missing_file_and_non_utf8(self):
        self.assertEqual(transcripts.parse_claude(self.home / "nope.jsonl").requests, [])
        p = self.home / "bad.jsonl"
        p.write_bytes(b"\xff\xfe garbage\n")
        self.assertEqual(transcripts.parse_claude(p).requests, [])

    def test_tool_results_join_their_tool_use(self):
        t = ClaudeTranscript()
        t.assistant([tool_use("tu1", "Bash", command="npm test"), tool_use("tu2", "Read", file_path="/w/b.ts")])
        t.user(tool_results=[("tu1", "3 failed"), ("tu2", "export {}")])
        t.assistant([text("ok")])
        s = transcripts.parse_claude(self.claude_file(t))
        uses = {u.id: u for u in s.requests[0].tool_uses}
        self.assertEqual(uses["tu1"].result.text, "3 failed")
        self.assertEqual(uses["tu1"].input, {"command": "npm test"})
        self.assertEqual([res.tool_use_id for res in s.requests[1].results_before], ["tu1", "tu2"])
        self.assertEqual(s.requests[1].user_text_before, 0)

    def test_tool_result_content_as_blocks(self):
        t = ClaudeTranscript()
        t.assistant([tool_use("tu1", "Read", file_path="/w/a.py")])
        t.lines.append(json.dumps({"type": "user", "isSidechain": False, "timestamp": "2027-01-15T08:00:00Z",
                                   "message": {"role": "user", "content": [{
                                       "type": "tool_result", "tool_use_id": "tu1",
                                       "content": [{"type": "text", "text": "line 1"}, {"type": "image"}]}]}}))
        s = transcripts.parse_claude(self.claude_file(t))
        self.assertEqual(s.requests[0].tool_uses[0].result.text, "line 1")

    def test_subagent_requests_are_flagged(self):
        t = ClaudeTranscript()
        t.assistant([text("main")], output=10)
        t.assistant([text("sub")], output=99, sidechain=True)
        t.assistant([text("main 2")], output=20)
        s = transcripts.parse_claude(self.claude_file(t))
        self.assertEqual([r.output for r in s.main], [10, 20])
        self.assertEqual(s.totals(main_only=True)["output"], 30)

    def test_compaction_boundary(self):
        t = ClaudeTranscript()
        t.assistant([text("a")])
        t.compact()
        t.assistant([text("b")])
        t.assistant([text("c")])
        s = transcripts.parse_claude(self.claude_file(t))
        self.assertEqual([r.compacted_before for r in s.requests], [False, True, False])
        self.assertEqual(s.compactions, 1)

    def test_timestamps_model_and_ttl(self):
        t = ClaudeTranscript(t0=1_800_000_000.0, model="claude-sonnet-5-5")
        t.assistant([text("a")], dt=0.5, cache_write=20000, cache_write_1h=20000)
        s = transcripts.parse_claude(self.claude_file(t))
        r = s.requests[0]
        self.assertAlmostEqual(r.ts, 1_800_000_000.5, places=2)
        self.assertEqual(r.model, "claude-sonnet-5-5")
        self.assertEqual(r.cache_write_1h, 20000)

    def test_speed_and_inference_geo(self):  # P8-T2: fast mode and US-only inference change the price
        t = ClaudeTranscript()
        t.assistant([text("a")], speed="fast", geo="us")
        t.assistant([text("b")], speed="standard", geo="global")
        t.assistant([text("c")])
        t.assistant([text("d")], speed=7, geo=["us"])
        s = transcripts.parse_claude(self.claude_file(t))
        self.assertEqual([(r.speed, r.geo) for r in s.requests],
                         [("fast", "us"), ("standard", "global"), ("", ""), ("", "")])

    def test_session_listing_and_days(self):
        for name, t0, project in (("old", 1_000_000_000.0, "-w-repo"), ("new", 1_800_000_000.0, "-w-other")):
            t = ClaudeTranscript(session=name, t0=t0)
            t.assistant([text("x")])
            path = self.claude_file(t, project=project)
            os.utime(path, (t.t, t.t))  # a transcript's mtime is its last write
        with mock.patch.object(transcripts.time, "time", return_value=1_800_000_100.0):
            self.assertEqual(sorted(s.id for s in transcripts.sessions(days=7)), ["new"])
            self.assertEqual(sorted(s.id for s in transcripts.sessions()), ["new", "old"])


class TestSubagentFiles(Home):  # P8-T1
    def session_with_agent(self):
        main = ClaudeTranscript()
        main.assistant([tool_use("ag", "Agent", prompt="look")], output=10)
        main.user(tool_results=[("ag", "found it")])
        main.assistant([text("done")], output=20)
        path = self.claude_file(main)
        sub = ClaudeTranscript(agent="a1")
        sub.user("look")
        sub.assistant([tool_use("b1", "Bash", command="npm test")], cache_read=500, output=30)
        sub.user(tool_results=[("b1", "3 failed")])
        sub.assistant([text("found it")], cache_read=900, output=40)
        sub.write(subagent_path(path, "a1"))
        return path

    def test_subagent_files_join_their_session(self):
        s = transcripts.parse_claude(self.session_with_agent())
        self.assertEqual([r.output for r in s.requests], [10, 20, 30, 40])
        self.assertEqual([r.output for r in s.main], [10, 20])
        self.assertEqual([r.chain for r in s.requests], ["", "", "agent-a1", "agent-a1"])
        self.assertTrue(all(r.sidechain for r in s.requests[2:]))
        self.assertEqual([[r.output for r in c] for c in s.chains], [[10, 20], [30, 40]])
        self.assertEqual(s.totals()["output"], 100)
        sub = s.chains[1]
        self.assertEqual(sub[0].user_text_before, len("look"))  # what was sent, per chain
        self.assertEqual([res.tool_use_id for res in sub[1].results_before], ["b1"])
        self.assertEqual(sub[0].tool_uses[0].result.text, "3 failed")
        self.assertEqual([res.tool_use_id for res in s.main[1].results_before], ["ag"])

    def test_listing_does_not_make_a_session_of_a_subagent_file(self):
        self.session_with_agent()
        ss = transcripts.sessions()
        self.assertEqual([(x.id, len(x.requests)) for x in ss], [("s1", 4)])

    def test_streamed_usage_takes_the_largest(self):
        main = ClaudeTranscript()
        main.assistant([text("a")], output=10)
        path = self.claude_file(main)
        sub = ClaudeTranscript(agent="a1")
        sub.assistant([thinking(), text("b"), tool_use("x", "Read", file_path="/w/a")], output=4030, partial_output=8)
        sub.write(subagent_path(path, "a1"))
        s = transcripts.parse_claude(path)
        self.assertEqual(s.chains[1][0].output, 4030)
        self.assertEqual([u.name for u in s.chains[1][0].tool_uses], ["Read"])

    def test_streamed_one_hour_write_takes_the_largest(self):  # P8-T2: the 1h share is priced on its own
        main = ClaudeTranscript()
        main.assistant([text("a")], output=10)
        path = self.claude_file(main)
        sub = ClaudeTranscript(agent="a1")
        sub.assistant([text("b")], cache_write=3000, cache_write_1h=3000)
        line = json.loads(sub.lines[0])
        line["message"]["usage"]["cache_creation"]["ephemeral_1h_input_tokens"] = 0
        sub.lines.insert(0, json.dumps(line))  # an earlier line of the same request, before the write was known
        sub.write(subagent_path(path, "a1"))
        r = transcripts.parse_claude(path).chains[1][0]
        self.assertEqual((r.cache_write, r.cache_write_1h), (3000, 3000))

    def test_request_in_both_files_counts_once(self):  # older Claude Code kept sidechains in the session file
        sub = ClaudeTranscript(agent="a1")
        sub.assistant([text("sub")], output=99)
        main = ClaudeTranscript()
        main.assistant([text("main")], output=10)
        main.lines += sub.lines
        path = self.claude_file(main)
        sub.write(subagent_path(path, "a1"))
        s = transcripts.parse_claude(path)
        self.assertEqual([r.output for r in s.requests], [10, 99])
        self.assertEqual([r.chain for r in s.requests], ["", "sidechain"])

    def test_inline_sidechain_is_its_own_chain(self):
        t = ClaudeTranscript()
        t.assistant([text("main")], output=10)
        t.assistant([text("sub")], output=99, sidechain=True)
        t.assistant([text("main 2")], output=20)
        s = transcripts.parse_claude(self.claude_file(t))
        self.assertEqual([[r.output for r in c] for c in s.chains], [[10, 20], [99]])

    def test_lines_of_an_inline_sidechain_request_are_merged(self):
        t = ClaudeTranscript()
        t.assistant([text("sub"), tool_use("x", "Bash", command="ls")], output=99, sidechain=True)
        s = transcripts.parse_claude(self.claude_file(t))
        self.assertEqual([u.name for u in s.requests[0].tool_uses], ["Bash"])

    def test_session_without_main_requests_has_an_empty_main_chain(self):
        main = ClaudeTranscript()
        main.user("hi")
        path = self.claude_file(main)
        sub = ClaudeTranscript(agent="a1")
        sub.assistant([text("sub")], output=5)
        sub.write(subagent_path(path, "a1"))
        s = transcripts.parse_claude(path)
        self.assertEqual([[r.output for r in c] for c in s.chains], [[], [5]])


class TestCodexParser(Home):
    def test_turns_calls_and_totals(self):
        r = CodexRollout()
        r.call("c1", ["bash", "-lc", "npm test"], "ok")
        r.turn(input_tokens=1000, cached=800, output=50)
        r.turn(input_tokens=1200, cached=1000, output=30)
        path = r.write(self.home / ".codex" / "sessions" / "2027" / "01" / "15" / "rollout-x.jsonl")
        s = transcripts.parse_codex(path)
        self.assertEqual(s.agent, "codex")
        self.assertEqual(s.totals(), {"input": 400, "cache_write": 0, "cache_read": 1800, "output": 80})
        call = s.requests[0].tool_uses[0]
        self.assertEqual((call.name, call.input["command"][-1], call.result.text), ("shell", "npm test", "ok"))
        self.assertEqual([x.id for x in transcripts.sessions(agents=("codex",))], ["c1"])

    def test_defensive(self):
        r = CodexRollout()
        r.lines += ["not json", '{"type": "event_msg", "payload": {"type": "token_count", "info": null}}',
                    '{"type": "response_item", "payload": {"type": "function_call_output"}}']
        r.turn(10, 0, 1)
        s = transcripts.parse_codex(r.write(self.home / "rollout-y.jsonl"))
        self.assertEqual(s.totals()["output"], 1)


def synthetic_calibration(n: int, ratios=(3.5, 1.7, 1.2), noise: float = 0.02, seed: int = 1,
                          scramble: bool = False) -> ClaudeTranscript:
    """n request pairs whose context grows by exactly chars/ratio per class (plus noise)."""
    rng = random.Random(seed)
    t = ClaudeTranscript()
    context, output = 5000, 40
    t.assistant([tool_use("t0", "Bash", command="x")], input=10, cache_read=context - 10, output=output)
    for i in range(n):
        d, h, v = rng.randint(200, 4000), rng.randint(0, 60), rng.randint(0, 400)
        body = ("word " * (d // 5))[:d] + "\n" + "".join(f"{rng.getrandbits(128):032x}" for _ in range(h)) + \
               "\n" + "ệ" * v
        from toknix.textutil import char_classes
        cc = char_classes(body)
        y = cc["default"] / ratios[0] + cc["hex"] / ratios[1] + cc["non_ascii"] / ratios[2]
        y = rng.uniform(50, 5000) if scramble else y * (1 + rng.uniform(-noise, noise))
        t.user(tool_results=[(f"t{i}", body)])
        context = context + output + int(round(y))
        t.assistant([tool_use(f"t{i + 1}", "Bash", command="x")], input=10, cache_read=context - 10, output=output)
    return t


class TestCalibration(Home):
    def test_recovers_known_ratios(self):
        s = transcripts.parse_claude(self.claude_file(synthetic_calibration(400)))
        cal = measure.calibrate([s])
        self.assertIsNotNone(cal)
        for cls, want in (("default", 3.5), ("hex", 1.7), ("non_ascii", 1.2)):
            with self.subTest(cls=cls):
                self.assertLess(abs(cal.ratios[cls] - want) / want, 0.05)
        self.assertLess(cal.median_error, 0.05)
        self.assertGreaterEqual(cal.samples, 200)

    def test_written_only_when_good(self):
        good = transcripts.parse_claude(self.claude_file(synthetic_calibration(400)))
        self.assertTrue(measure.update_calibration([good]))
        saved = json.loads((self.home / ".toknix" / "calibration.json").read_text())["claude"]
        self.assertLess(abs(saved["ratios"]["default"] - 3.5) / 3.5, 0.05)

    def test_too_few_samples(self):
        s = transcripts.parse_claude(self.claude_file(synthetic_calibration(150)))
        self.assertFalse(measure.update_calibration([s]))
        self.assertFalse((self.home / ".toknix" / "calibration.json").exists())

    def test_bad_fit(self):
        s = transcripts.parse_claude(self.claude_file(synthetic_calibration(400, scramble=True)))
        self.assertFalse(measure.update_calibration([s]))
        self.assertFalse((self.home / ".toknix" / "calibration.json").exists())

    def test_reports_use_it_filters_never_do(self):
        (self.home / ".toknix").mkdir()
        (self.home / ".toknix" / "calibration.json").write_text(json.dumps(
            {"claude": {"ratios": {"default": 1.0, "hex": 1.0, "non_ascii": 1.0}, "samples": 300,
                        "median_error": 0.01}}))
        self.assertEqual(measure.report_ratios(config.load())["default"], 1.0)
        lines = [f"line {i} " + "x" * 80 for i in range(300)]
        with_cal = filters.apply("make", lines, 0, config.load()).lines
        (self.home / ".toknix" / "calibration.json").unlink()
        self.assertEqual(with_cal, filters.apply("make", lines, 0, config.load()).lines)
        self.assertEqual(measure.report_ratios(config.load())["default"], 4.0)


if __name__ == "__main__":
    unittest.main()
