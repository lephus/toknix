"""P4-T2: report, waste, context, snapshot and the transcript parts of gain (A6.2, A6.5, A6.6, N1.1, N1.3, N1.5a)."""
from __future__ import annotations

import io
import json
import os
import statistics
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from toknix import cli, reports, store
from transcript_factory import ClaudeTranscript, CodexRollout, subagent_path, text, thinking, tool_use

NOW = 1_800_000_000.0
# ETU per token of claude-opus-5-5, the factory's model, with 5-minute cache writes: input, cache write, cache read,
# output (A1, P8-T2: 1 ETU = one input token of etu_model, claude-opus-5-5 by default)
W = [1.0, 1.25, 0.05, 5.0]
HAIKU = [0.1 / 4, 0.125 / 4, 0.2 / 4, 0.01 / 4, 0.5 / 4]  # claude-haiku-5-5, prompt ≤ 100k: 5 kinds (A1)


def etu(i, cw, cr, o):
    return W[0] * i + W[1] * cw + W[2] * cr + W[3] * o


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
        for target in ("toknix.transcripts.time.time", "toknix.reports.time.time"):
            p = mock.patch(target, return_value=NOW + 3600)
            p.start()
            self.addCleanup(p.stop)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        self.project = self.home / "work" / "repo"
        self.project.mkdir(parents=True)
        os.chdir(self.project)

    def save(self, t: ClaudeTranscript, project: str = "-work-repo") -> Path:
        path = t.write(self.home / ".claude" / "projects" / project / f"{t.session}.jsonl")
        os.utime(path, (t.t, t.t))
        return path

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def json_of(self, *argv: str) -> dict:
        code, out = self.run_cli(*argv, "--json")
        self.assertEqual(code, 0, out)
        return json.loads(out)


class TestReport(Home):
    def make(self):
        a = ClaudeTranscript(session="a", t0=NOW)
        a.assistant([text("x" * 400)], input=100, cache_read=1000, cache_write=200, output=100)
        a.assistant([text("y")], input=50, cache_read=1300, cache_write=0, output=50)
        a.assistant([text("sub")], input=999, cache_read=999, cache_write=999, output=999, sidechain=True)
        self.save(a)
        b = ClaudeTranscript(session="b", t0=NOW)
        b.assistant([tool_use("t1", "Bash", command="ls")], input=10, cache_read=0, cache_write=1000, output=10)
        b.user(tool_results=[("t1", "z" * 4000)])
        b.assistant([text("ok")], input=10, cache_read=1000, cache_write=1000, output=10)
        self.save(b, project="-work-other")

    def test_totals_etu_cache_hit_output_share(self):
        self.make()
        d = self.json_of("report", "--days", "7")
        self.assertEqual(d["sessions"], 2)
        self.assertEqual(d["requests"], 4)  # main-chain requests; the subagent's cost is counted (P8-T1)
        self.assertEqual(d["tokens"], {"input": 1169, "cache_write": 3199, "cache_read": 4299, "output": 1169})
        total = etu(1169, 3199, 4299, 1169)
        sub = etu(999, 999, 999, 999)
        self.assertAlmostEqual(d["etu"], total)
        self.assertAlmostEqual(d["etu_per_session"], total / 2)
        self.assertAlmostEqual(d["etu_per_request"], total / 5)
        self.assertAlmostEqual(d["cache_hit"], 4299 / (1169 + 3199 + 4299))
        self.assertAlmostEqual(d["output_share"], 1169 * 5 / total)
        self.assertEqual((d["subagents"]["chains"], d["subagents"]["requests"]), (1, 1))
        self.assertAlmostEqual(d["subagents"]["etu"], sub)
        self.assertAlmostEqual(d["subagents"]["usd"], 999 * (4 + 5 + 0.2 + 20) / 1e6)  # P8-T2: Opus 5.5 prices
        self.assertAlmostEqual(d["subagents"]["share"], sub / total)
        self.assertEqual(d["tool_output_tokens"], 1000)  # 4000 chars / 4
        top = {s["id"]: s for s in d["top_sessions"]}
        self.assertEqual([s["id"] for s in d["top_sessions"]], ["a", "b"])
        self.assertAlmostEqual(top["a"]["etu"], etu(150, 200, 2300, 150) + sub)
        self.assertAlmostEqual(top["a"]["subagent_share"], sub / top["a"]["etu"])
        self.assertEqual(top["b"]["subagent_share"], 0)

    def test_output_breakdown_with_hidden_thinking(self):  # A6.6
        t = ClaudeTranscript(session="h", t0=NOW)
        t.assistant([thinking(""), text("t" * 400), tool_use("w1", "Write", file_path="/a", content="c" * 780)],
                    output=1000)
        self.save(t)
        b = self.json_of("report")["output_breakdown"]
        write_chars = len(json.dumps({"file_path": "/a", "content": "c" * 780}))
        self.assertEqual(b["text"], 100)
        self.assertEqual(b["write_tools"], write_chars // 4)
        self.assertEqual(b["thinking"], 1000 - 100 - write_chars // 4)  # hidden: the rest of output_tokens
        self.assertEqual(b["other_tools"], 0)

    def test_output_breakdown_with_visible_thinking(self):
        t = ClaudeTranscript(session="v", t0=NOW)
        t.assistant([thinking("k" * 1200), text("t" * 400)], output=400)
        self.save(t)
        b = self.json_of("report")["output_breakdown"]
        self.assertEqual((b["thinking"], b["text"]), (300, 100))  # by chars, scaled to output_tokens

    def test_text_report_and_no_data(self):
        code, out = self.run_cli("report")
        self.assertEqual(code, 0)
        self.assertIn("no transcripts in the last 7 days", out)
        self.make()
        out = self.run_cli("report")[1]
        self.assertIn("output share", out)
        self.assertIn("cache hit", out)
        self.assertIn("calibration:", out)


class TestWaste(Home):
    def session_with_tools(self) -> ClaudeTranscript:
        t = ClaudeTranscript(session="w", t0=NOW)
        t.assistant([tool_use("b1", "Bash", command="npm test")], cache_read=100)
        t.user(tool_results=[("b1", "x" * 24000)])                         # 6,000 tokens, early: R = 4
        t.assistant([tool_use("r1", "Read", file_path="/w/src/app.ts")], cache_read=200)
        t.user(tool_results=[("r1", "y" * 800)])                           # 200 tokens, R = 3
        t.assistant([tool_use("r2", "Read", file_path="/w/node_modules/x/index.js")], cache_read=300)
        t.user(tool_results=[("r2", "z" * 400)])
        t.assistant([tool_use("r3", "Read", file_path="/w/src/app.ts")], cache_read=400)
        t.user(tool_results=[("r3", "y" * 800)])
        t.assistant([tool_use("r4", "Read", file_path="/w/src/app.ts")], cache_read=500)
        t.user(tool_results=[("r4", "y" * 800)])
        t.assistant([text("done")], cache_read=600)
        return t

    def test_eight_groups_each_with_a_hint(self):
        self.save(self.session_with_tools())
        d = self.json_of("waste")
        self.assertEqual(list(d["groups"]), ["tool_families", "big_outputs", "junk", "repeated_reads",
                                             "long_sessions", "cache_breaks", "output_heavy", "subagents"])
        for name, g in d["groups"].items():
            with self.subTest(group=name):
                self.assertTrue(g["hint"])
        out = self.run_cli("waste")[1]
        for n in range(1, 9):
            self.assertIn(f"({n})", out)

    def test_tool_families_and_big_outputs_by_reread_cost(self):
        self.save(self.session_with_tools())
        g = self.json_of("waste")["groups"]
        fam = g["tool_families"]["items"]
        self.assertEqual(fam[0]["family"], "Bash:npm")
        self.assertEqual(fam[0]["tokens"], 6000)
        self.assertAlmostEqual(fam[0]["etu"], 6000 * W[1] + 6000 * W[2] * 4)  # R = requests left after it
        self.assertEqual(fam[1]["family"], "Read:/w/src/app.ts")
        self.assertEqual(fam[1]["tokens"], 600)
        self.assertEqual([x["etu"] for x in fam], sorted((x["etu"] for x in fam), reverse=True))
        big = g["big_outputs"]["items"]
        self.assertEqual([(b["family"], b["tokens"], b["remaining_requests"]) for b in big], [("Bash:npm", 6000, 4)])

    def test_compaction_resets_remaining_requests(self):
        t = ClaudeTranscript(session="c", t0=NOW)
        t.assistant([tool_use("b1", "Bash", command="pytest")])
        t.user(tool_results=[("b1", "x" * 24000)])
        t.assistant([text("a")])
        t.compact()
        t.assistant([text("b")])
        t.assistant([text("c")])
        self.save(t)
        big = self.json_of("waste")["groups"]["big_outputs"]["items"]
        self.assertEqual(big[0]["remaining_requests"], 0)  # the request carrying it, then a compaction

    def test_junk_repeated_reads_long_sessions(self):
        self.save(self.session_with_tools())
        long = ClaudeTranscript(session="long", t0=NOW)
        for _ in range(81):
            long.assistant([text("x")])
        self.save(long, project="-work-long")
        g = self.json_of("waste")["groups"]
        self.assertEqual([j["path"] for j in g["junk"]["items"]], ["/w/node_modules/x/index.js"])
        self.assertEqual(g["repeated_reads"]["items"], [{"session": "w", "path": "/w/src/app.ts", "reads": 3}])
        self.assertEqual([s["session"] for s in g["long_sessions"]["items"]], ["long"])

    def test_cache_breaks_all_four_causes(self):  # A6.5
        t = ClaudeTranscript(session="cb", t0=NOW)
        t.assistant([text("1")], cache_read=50000, cache_write=1000)
        t.compact()
        t.assistant([text("2 compact")], cache_read=0, cache_write=30000)
        t.assistant([text("3")], cache_read=30000, cache_write=500)
        t.assistant([text("4 model")], cache_read=0, cache_write=40000, model="claude-haiku-5-5")
        t.assistant([text("5")], cache_read=40000, cache_write=500, model="claude-haiku-5-5")
        t.assistant([text("6 idle")], cache_read=0, cache_write=20000, model="claude-haiku-5-5", dt=600)
        t.assistant([text("7")], cache_read=20000, cache_write=500, model="claude-haiku-5-5")
        t.assistant([text("8 unknown")], cache_read=0, cache_write=15000, model="claude-haiku-5-5")
        t.assistant([text("9 small")], cache_read=0, cache_write=9000, model="claude-haiku-5-5")
        self.save(t)
        g = self.json_of("waste")["groups"]["cache_breaks"]
        self.assertEqual([b["cause"] for b in g["items"]], ["compact", "model", "idle", "unknown"])
        lost = {b["cause"]: b["etu_lost"] for b in g["items"]}
        self.assertEqual(lost["compact"], 0)  # normal after a compaction, not waste
        self.assertAlmostEqual(lost["model"], 40000 * (HAIKU[1] - HAIKU[3]))  # priced as the request's model
        self.assertAlmostEqual(g["etu_lost"], (40000 + 20000 + 15000) * (HAIKU[1] - HAIKU[3]))

    def test_one_hour_ttl_is_not_idle(self):
        t = ClaudeTranscript(session="ttl", t0=NOW)
        t.assistant([text("1")], cache_read=40000, cache_write=1000, cache_write_1h=1000)
        t.assistant([text("2")], cache_read=0, cache_write=40000, dt=600)  # 10 min < 1 h TTL
        self.save(t)
        causes = [b["cause"] for b in self.json_of("waste")["groups"]["cache_breaks"]["items"]]
        self.assertEqual(causes, ["unknown"])

    def test_output_heavy_sessions(self):
        heavy = ClaudeTranscript(session="heavy", t0=NOW)
        heavy.assistant([text("w" * 4000)], input=100, cache_read=1000, output=1000)
        self.save(heavy)
        light = ClaudeTranscript(session="light", t0=NOW)
        light.assistant([text("w")], input=100, cache_read=100000, output=10)
        self.save(light, project="-work-light")
        items = self.json_of("waste")["groups"]["output_heavy"]["items"]
        self.assertEqual([i["session"] for i in items], ["heavy"])
        self.assertIn("breakdown", items[0])


class TestSubagents(Home):  # P8-T1
    def save_agent(self, session_file: Path, agent: ClaudeTranscript) -> Path:
        return agent.write(subagent_path(session_file, agent.agent))

    def test_report_counts_subagent_files(self):
        main = ClaudeTranscript(session="m", t0=NOW)
        main.assistant([tool_use("ag", "Agent", prompt="x")], input=10, cache_read=1000, output=10)
        main.user(tool_results=[("ag", "done")])
        main.assistant([text("ok")], input=10, cache_read=1000, output=10)
        path = self.save(main)
        sub = ClaudeTranscript(session="m", t0=NOW, agent="a1")
        sub.assistant([text("a")], cache_write=5000, output=3000, partial_output=8)
        sub.assistant([text("b")], cache_read=5000, output=100)
        self.save_agent(path, sub)
        d = self.json_of("report")
        self.assertEqual(d["requests"], 2)
        self.assertEqual(d["tokens"]["output"], 3120)  # streamed usage: the largest line counts
        self.assertEqual(d["subagents"]["chains"], 1)
        self.assertEqual(d["subagents"]["requests"], 2)
        self.assertAlmostEqual(d["subagents"]["etu"], etu(20, 5000, 5000, 3100))  # input 10 per request
        out = self.run_cli("report")[1]
        self.assertIn("subagents: 1 agents, 2 requests", out)

    def test_waste_rereads_are_counted_in_their_own_chain(self):
        main = ClaudeTranscript(session="m", t0=NOW)
        for _ in range(5):
            main.assistant([text("main")], cache_read=1000)
        path = self.save(main)
        sub = ClaudeTranscript(session="m", t0=NOW, agent="a1")
        sub.assistant([tool_use("b1", "Bash", command="npm test")], cache_read=100)
        sub.user(tool_results=[("b1", "x" * 24000)])  # 6,000 tokens; one more request of the subagent after it
        sub.assistant([tool_use("r1", "Read", file_path="/w/node_modules/x/a.js")], cache_read=0, cache_write=20000)
        sub.user(tool_results=[("r1", "y")])
        sub.assistant([text("done")], cache_read=20000)
        self.save_agent(path, sub)
        g = self.json_of("waste")["groups"]
        big = g["big_outputs"]["items"]
        self.assertEqual([(b["family"], b["tokens"], b["remaining_requests"]) for b in big], [("Bash:npm", 6000, 1)])
        self.assertAlmostEqual(big[0]["etu"], 6000 * W[1] + 6000 * W[2] * 1)
        self.assertEqual([j["path"] for j in g["junk"]["items"]], ["/w/node_modules/x/a.js"])
        # the subagent's first request starts its own cache: not a break of the main chain
        self.assertEqual([b["cause"] for b in g["cache_breaks"]["items"]], ["unknown"])

    def test_repeated_reads_per_chain(self):
        main = ClaudeTranscript(session="m", t0=NOW)
        for i in range(2):
            main.assistant([tool_use(f"r{i}", "Read", file_path="/w/a.py")])
            main.user(tool_results=[(f"r{i}", "a")])
        path = self.save(main)
        for agent in ("a1", "a2"):
            sub = ClaudeTranscript(session="m", t0=NOW, agent=agent)
            sub.assistant([tool_use(f"{agent}r", "Read", file_path="/w/a.py")])
            self.save_agent(path, sub)
        self.assertEqual(self.json_of("waste")["groups"]["repeated_reads"]["items"], [])

    def test_long_session_and_output_share_include_subagents(self):
        main = ClaudeTranscript(session="m", t0=NOW)
        for _ in range(81):
            main.assistant([text("x")], input=0, cache_read=1000, output=0)
        path = self.save(main)
        sub = ClaudeTranscript(session="m", t0=NOW, agent="a1")
        sub.assistant([text("w" * 4000)], input=0, output=10000)
        self.save_agent(path, sub)
        g = self.json_of("waste")["groups"]
        self.assertEqual(g["long_sessions"]["items"][0]["requests"], 81)
        self.assertAlmostEqual(g["long_sessions"]["items"][0]["etu"], 81 * 1000 * W[2] + 10000 * W[3])
        self.assertEqual([i["session"] for i in g["output_heavy"]["items"]], ["m"])

    def test_context_and_gain_see_subagent_calls(self):
        main = ClaudeTranscript(session="m", t0=NOW)
        main.assistant([text("main")])
        path = self.save(main)
        sub = ClaudeTranscript(session="m", t0=NOW, agent="a1")
        sub.assistant([tool_use("s1", "Skill", skill="pdf"), tool_use("b1", "Bash", command="npm test")])
        sub.user(tool_results=[("b1", "ok")])
        sub.assistant([text("end")])
        self.save_agent(path, sub)
        root = self.home / ".claude" / "skills" / "pdf"
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text("---\nname: pdf\n---\n")
        skills = {x["name"]: x["calls"] for x in self.json_of("context")["skills"]}
        self.assertEqual(skills, {"pdf": 1})
        stats = reports.bash_stats(7)
        self.assertEqual((stats["r_values"], stats["bash_calls"]), ([0], 1))


class TestPrices(Home):  # P8-T2: each request at its model's price (A1)
    def mixed(self):
        main = ClaudeTranscript(session="p", t0=NOW)
        main.assistant([text("a")], input=1000, cache_write=10000, cache_write_1h=10000, output=1000)  # 26,000 ETU
        main.assistant([text("b")], input=0, cache_read=10000, output=500)                             # 3,000
        path = self.save(main)
        sub = ClaudeTranscript(session="p", t0=NOW, agent="a1", model="claude-sonnet-5-5")
        sub.assistant([text("c")], input=0, cache_write=4000, output=2000)                             # 7,500
        sub.write(subagent_path(path, "a1"))
        codex = CodexRollout(session="c1", t0=NOW)
        codex.turn(1000, 0, 100)                                             # no price: weights → 1,500
        os.utime(codex.write(self.home / ".codex" / "sessions" / "2027" / "rollout-c1.jsonl"), (NOW, NOW))

    def test_report_usd_by_model_and_unpriced(self):
        self.mixed()
        d = self.json_of("report")
        self.assertAlmostEqual(d["etu"], 38000)
        self.assertAlmostEqual(d["usd"], (4000 + 80000 + 20000 + 2000 + 10000 + 10000 + 20000) / 1e6)
        self.assertEqual((d["etu_model"], d["etu_model_usd"]), ("claude-opus-5-5", 4))
        self.assertAlmostEqual(d["output_share"], (5000 + 2500 + 5000 + 500) / 38000)
        self.assertAlmostEqual(d["subagents"]["etu"], 7500)
        models = [(m["model"], m["requests"], round(m["etu"]), m["usd"] and round(m["usd"], 6)) for m in d["models"]]
        self.assertEqual(models, [("claude-opus-5-5", 2, 29000, 0.116), ("claude-sonnet-5-5", 1, 7500, 0.03),
                                  ("(unknown)", 1, 1500, None)])
        self.assertAlmostEqual(d["models"][0]["share"], 29000 / 38000)
        self.assertEqual(d["unpriced"], {"requests": 1, "models": ["(unknown)"]})
        top = {s["id"]: s for s in d["top_sessions"]}
        self.assertAlmostEqual(top["p"]["usd"], 0.146)
        self.assertIsNone(top["c1"]["usd"])
        out = self.run_cli("report")[1]
        self.assertIn("38.0k ETU ≈ $0.146 at API list prices", out)
        self.assertIn("1 ETU = one claude-opus-5-5 input token ($4.00/MTok)", out)
        self.assertIn("claude-sonnet-5-5", out.split("By model")[1])
        self.assertIn("no price for 1 requests ((unknown))", out)

    def test_waste_rereads_at_the_one_hour_write_price(self):
        t = ClaudeTranscript(session="h", t0=NOW)
        t.assistant([tool_use("b1", "Bash", command="npm test")])
        t.user(tool_results=[("b1", "x" * 24000)])                              # 6,000 tokens
        t.assistant([text("seen")], cache_write=6000, cache_write_1h=6000)      # writes it for 1 hour
        t.assistant([text("again")], cache_read=6000)                           # R = 1
        self.save(t)
        g = self.json_of("waste")["groups"]
        fam = g["tool_families"]["items"][0]
        self.assertAlmostEqual(fam["etu"], 6000 * 2 + 6000 * 0.05 * 1)
        self.assertAlmostEqual(fam["usd"], (6000 * 8 + 6000 * 0.2) / 1e6)
        self.assertAlmostEqual(g["big_outputs"]["items"][0]["usd"], fam["usd"])
        self.assertIn("$0.049", self.run_cli("waste")[1])

    def test_cache_break_lost_at_the_one_hour_write_price(self):
        t = ClaudeTranscript(session="b", t0=NOW)
        t.assistant([text("1")], cache_read=50000, cache_write=1000)
        t.assistant([text("2")], cache_read=0, cache_write=40000, cache_write_1h=40000)
        self.save(t)
        g = self.json_of("waste")["groups"]["cache_breaks"]
        self.assertAlmostEqual(g["etu_lost"], 40000 * 2 - 40000 * 0.05)
        self.assertAlmostEqual(g["usd_lost"], (40000 * 8 - 40000 * 0.2) / 1e6)
        self.assertIn("$0.312 lost", self.run_cli("waste")[1])


class TestSubagentGroups(Home):  # P8-T3
    def agent(self, session_file: Path, agent: str, meta: dict | None, model: str = "claude-opus-5-5"):
        t = ClaudeTranscript(session="m", t0=NOW, agent=agent, model=model)
        path = subagent_path(session_file, agent)
        if meta is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.with_name(f"agent-{agent}.meta.json").write_text(json.dumps(meta))
        return t, path

    def make(self):
        main = ClaudeTranscript(session="m", t0=NOW)
        main.assistant([text("main")], input=0, cache_read=20000, output=100)
        session = self.save(main)
        t, path = self.agent(session, "a1", {"agentType": "general-purpose", "model": "opus"})
        t.assistant([tool_use("r1", "Read", file_path="/w/a.py")], input=0, cache_write=10000, output=1000)
        t.user(tool_results=[("r1", "x")])
        t.assistant([text("found")], input=0, cache_read=10000, output=1000)     # only read: $0.092
        t.write(path)
        t, path = self.agent(session, "a2", {"agentType": "general-purpose"})    # meta without a model
        t.assistant([tool_use("e1", "Edit", file_path="/w/a.py")], input=0, cache_write=10000, output=1000)
        t.write(path)                                                            # edits: $0.07
        t, path = self.agent(session, "a3", {"agentType": "Explore", "model": "sonnet"}, "claude-sonnet-5-5")
        t.assistant([text("x")], input=0, cache_write=4000, output=0)
        t.write(path)
        t, path = self.agent(session, "a4", {"agentType": "fork", "model": "inherit"})
        t.assistant([text("x")], input=0, cache_read=100000, output=100)        # reads the parent's cache
        t.write(path)
        t, path = self.agent(session, "a5", None, "claude-haiku-5-5")            # no meta file
        t.assistant([text("x")], input=0, cache_write=1000, output=10)
        t.write(path)
        (path.parent / "agent-a6.meta.json").write_text("{broken")
        t, path = self.agent(session, "a6", None)
        t.assistant([text("x")], input=0, cache_write=1000, output=10)
        t.write(path)

    def test_groups_by_type_and_model(self):
        self.make()
        g = self.json_of("waste")["groups"]["subagents"]
        rows = {(x["agent_type"], x["model"]): x for x in g["items"]}
        self.assertEqual(list(rows), [("general-purpose", "claude-opus-5-5"), ("fork", "claude-opus-5-5"),
                                      ("Explore", "claude-sonnet-5-5"), ("?", "claude-opus-5-5"),
                                      ("?", "claude-haiku-5-5")])  # by ETU
        gp = rows["general-purpose", "claude-opus-5-5"]
        self.assertEqual((gp["agents"], gp["requests"], gp["requests_per_agent"]), (2, 3, 1.5))
        self.assertAlmostEqual(gp["usd"], 0.092 + 0.07)
        self.assertAlmostEqual(gp["etu"], (0.092 + 0.07) * 1e6 / 4)
        self.assertEqual(gp["no_edit_agents"], 1)
        self.assertAlmostEqual(gp["no_edit_usd"], 0.092)
        # the read-only agent's tokens at Sonnet 5.5 prices: (10000·2.5 + 1000·10 + 10000·0.1 + 1000·10) / 1e6
        self.assertEqual(gp["cheaper_model"], "claude-sonnet-5-5")
        self.assertAlmostEqual(gp["saving_usd"], 0.092 - 0.046)
        total = sum(x["etu"] for x in g["items"]) + (20000 * 0.05 + 100 * 5)  # + the main chain
        self.assertAlmostEqual(gp["share"], gp["etu"] / total)
        self.assertIsNone(rows["fork", "claude-opus-5-5"]["saving_usd"])       # keeps the parent's cache
        self.assertIsNone(rows["Explore", "claude-sonnet-5-5"]["saving_usd"])  # already the cheaper model
        self.assertIsNone(rows["?", "claude-haiku-5-5"]["saving_usd"])
        self.assertAlmostEqual(rows["?", "claude-opus-5-5"]["saving_usd"],
                               (1000 * 5 + 10 * 20 - 1000 * 2.5 - 10 * 10) / 1e6)
        self.assertEqual(g["agents"], 6)
        self.assertAlmostEqual(g["saving_usd"], 0.046 + (1000 * 5 + 10 * 20 - 1000 * 2.5 - 10 * 10) / 1e6)

    def test_text_and_cheaper_model_from_config(self):
        self.make()
        out = self.run_cli("waste")[1].split("(8)")[1]
        self.assertIn("subagents by type and model (6 agents", out)
        self.assertIn("general-purpose / claude-opus-5-5", out)
        self.assertIn("claude-sonnet-5-5 would cost ~$0.046 less", out)
        (self.home / ".toknix").mkdir(exist_ok=True)
        (self.home / ".toknix" / "config.json").write_text(json.dumps({"waste_subagent_model": "claude-haiku-5-5"}))
        rows = {(x["agent_type"], x["model"]): x for x in self.json_of("waste")["groups"]["subagents"]["items"]}
        self.assertEqual(rows["Explore", "claude-sonnet-5-5"]["cheaper_model"], "claude-haiku-5-5")
        self.assertAlmostEqual(rows["Explore", "claude-sonnet-5-5"]["saving_usd"], 4000 * (2.5 - 0.125) / 1e6)

    def test_no_subagents(self):
        t = ClaudeTranscript(session="solo", t0=NOW)
        t.assistant([text("x")])
        self.save(t)
        g = self.json_of("waste")["groups"]["subagents"]
        self.assertEqual((g["items"], g["agents"], g["saving_usd"]), ([], 0, None))
        self.assertIn("(8) subagents by type and model (0 agents", self.run_cli("waste")[1])


class TestContext(Home):
    def test_instruction_files_mcp_and_skills(self):
        claude = self.home / ".claude"
        (claude / "CLAUDE.md").parent.mkdir(parents=True, exist_ok=True)
        (claude / "CLAUDE.md").write_text("g" * 4000)                                   # 1,000 tokens
        (self.project / "CLAUDE.md").write_text("p" * 2400)                             # 600
        (self.project / ".claude" / "rules").mkdir(parents=True)
        (self.project / ".claude" / "rules" / "style.md").write_text("r" * 400)          # 100
        (self.project / "AGENTS.md").write_text("w" * 40)  # not "a": hex runs count double (A1)
        (self.home / ".claude.json").write_text(json.dumps({
            "mcpServers": {"github": {"command": "gh-mcp"}, "unused": {"command": "x"}},
            "projects": {str(self.project): {"mcpServers": {"local-db": {"command": "db"}}}}}))
        (self.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"figma": {"url": "https://x"}}}))
        for root, name in ((claude / "skills" / "pdf", "pdf"), (self.project / ".claude" / "skills" / "deploy", "deploy"),
                           (claude / "plugins" / "cache" / "m" / "p" / "1.0" / "skills" / "lint", "lint")):
            root.mkdir(parents=True)
            (root / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\nbody\n")
        t = ClaudeTranscript(session="ctx", t0=NOW)
        t.assistant([tool_use("m1", "mcp__github__get_issue", number=1), tool_use("m2", "mcp__github__list", q="x"),
                     tool_use("s1", "Skill", skill="pdf")])
        self.save(t)
        d = self.json_of("context")
        files = {Path(f["path"]).name if Path(f["path"]).parent != claude else "global CLAUDE.md": f["tokens"]
                 for f in d["instruction_files"]}
        self.assertEqual(files, {"global CLAUDE.md": 1000, "CLAUDE.md": 600, "style.md": 100, "AGENTS.md": 10})
        self.assertEqual(d["instruction_tokens"], 1710)
        self.assertTrue(d["warn"])
        servers = {m["name"]: m["calls"] for m in d["mcp_servers"]}
        self.assertEqual(servers, {"github": 2, "unused": 0, "local-db": 0, "figma": 0})
        skills = {s["name"]: s["calls"] for s in d["skills"]}
        self.assertEqual(skills, {"pdf": 1, "deploy": 0, "lint": 0})
        out = self.run_cli("context")[1]
        self.assertIn("unused", out)
        self.assertIn("not called in 30 days", out)


class TestSnapshot(Home):
    def test_writes_a_labelled_summary(self):
        t = TestWaste.session_with_tools(self)
        self.save(t)
        code, out = self.run_cli("snapshot", "--label", "before")
        self.assertEqual(code, 0)
        path = self.home / ".toknix" / "results" / "snapshot-before.json"
        d = json.loads(path.read_text())
        self.assertEqual(d["label"], "before")
        self.assertEqual(d["waste"]["big_outputs"], 1)
        self.assertIn("etu", d["report"])
        self.assertAlmostEqual(d["report"]["usd"], d["report"]["etu"] * 4 / 1e6)  # P8-T2

    def test_label_is_a_safe_file_name(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["snapshot", "--label", "../x"]), 2)


class TestGainFromTranscripts(Home):
    def test_r_bar_and_wrapped_share(self):
        t = ClaudeTranscript(session="g", t0=NOW)
        for i in range(4):
            cmd = f"toknix run -- 'npm test {i}'" if i < 3 else "npm test"
            t.assistant([tool_use(f"b{i}", "Bash", command=cmd)])
            t.user(tool_results=[(f"b{i}", "ok")])
        t.assistant([text("end")])
        self.save(t)
        store.record_run("pytest", "pytest", 1, 1000, 100, 50, 10, "a0", 0,
                         {"default": 900, "hex": 0, "non_ascii": 0})
        d = json.loads(self.run_cli("gain", "--json")[1])
        # Bash results sent with requests 2..5 of 5: 3, 2, 1, 0 requests left → median 1.5
        self.assertEqual(d["reread"]["r_bar"], statistics.median([3, 2, 1, 0]))
        self.assertEqual(d["reread"]["r_bar_source"], "transcripts")
        # P8-T2: median of w_cw + w_cr·R over those results, with the weights of the request that carried each
        self.assertAlmostEqual(d["reread"]["multiplier"], statistics.median(W[1] + W[2] * r for r in [3, 2, 1, 0]))
        self.assertEqual(d["wrapped"], {"bash_calls": 4, "wrapped": 3, "share": 0.75})


if __name__ == "__main__":
    unittest.main()
