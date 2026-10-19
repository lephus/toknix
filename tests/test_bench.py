"""P4-T3: `bench` and `compare` (A6.3, N1.5c), with a fake `claude -p` from tests/fake_agents/."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support

from tokopt import cli

FAKE = str(Path(support.ROOT) / "tests" / "fake_agents" / "fake_claude.py")
CHECK = [sys.executable, "-c", "import os, sys; sys.exit(0 if os.path.exists('done.txt') else 1)"]


class BenchCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("TOKOPT_", "FAKE_")) and k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        patcher = mock.patch.dict(os.environ, dict(env, HOME=str(self.tmp / "home"),
                                                   USERPROFILE=str(self.tmp / "home")), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("# sample\n")
        genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                    GIT_COMMITTER_EMAIL="t@t")
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, env=genv)

    def tasks(self, repeat: int = 4, timeout: int = 60, n_tasks: int = 3) -> Path:
        spec = {"repo": str(self.repo), "ref": "HEAD", "agent": "claude", "repeat": repeat, "timeout": timeout,
                "agent_cmd": [sys.executable, FAKE], "setup": [],
                "tasks": [{"id": f"t{i}", "prompt": f"fix bug {i}", "check": CHECK} for i in range(n_tasks)]}
        path = self.tmp / "tasks.json"
        path.write_text(json.dumps(spec))
        return path

    def bench(self, label: str, *extra: str, **fake_env) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {f"FAKE_{k.upper()}": str(v) for k, v in fake_env.items()}), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["bench", str(self.tasks_path), "--label", label, *extra])
        return code, out.getvalue(), err.getvalue()

    def compare(self, a: str, b: str) -> dict:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["compare", a, b, "--json"]), 0)
        return json.loads(out.getvalue())

    def results(self, label: str) -> list[dict]:
        path = self.tmp / "home" / ".tokopt" / "results" / f"bench-{label}.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()]

    def worktrees(self) -> list[str]:
        out = subprocess.run(["git", "-C", str(self.repo), "worktree", "list", "--porcelain"], capture_output=True,
                             text=True).stdout
        return [l for l in out.splitlines() if l.startswith("worktree ")]


class TestBench(BenchCase):
    def test_runs_records_and_cleans_up(self):
        self.tasks_path = self.tasks(repeat=2, n_tasks=2)
        code, out, err = self.bench("a")
        self.assertEqual(code, 0, err)
        rows = self.results("a")
        self.assertEqual([(r["task"], r["repeat"]) for r in rows], [("t0", 0), ("t0", 1), ("t1", 0), ("t1", 1)])
        r = rows[0]
        self.assertTrue(r["passed"])
        self.assertEqual(r["usage"], {"input": 2000, "cache_write": 10000, "cache_read": 80000, "output": 3000})
        self.assertAlmostEqual(r["etu"], 2000 + 1.25 * 10000 + 0.1 * 80000 + 5 * 3000)
        self.assertEqual((r["cost"], r["turns"]), (0.5, 7))
        self.assertEqual(self.worktrees(), [f"worktree {self.repo}"])  # every worktree removed

    def test_failed_check(self):
        self.tasks_path = self.tasks(repeat=1, n_tasks=1)
        self.bench("f", **{"pass": "none"})
        self.assertFalse(self.results("f")[0]["passed"])

    def test_timeout_still_removes_the_worktree(self):
        self.tasks_path = self.tasks(repeat=1, n_tasks=1, timeout=1)
        code, _, err = self.bench("slow", sleep=5)
        self.assertEqual(code, 0, err)
        r = self.results("slow")[0]
        self.assertTrue(r["timeout"])
        self.assertFalse(r["passed"])
        self.assertEqual(self.worktrees(), [f"worktree {self.repo}"])

    def test_rules_reach_the_worktree_and_global_block_warns(self):
        self.tasks_path = self.tasks(repeat=1, n_tasks=1)
        rules = self.tmp / "terse.md"
        rules.write_text("Be brief.\n")
        claude_md = self.tmp / "home" / ".claude" / "CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("<!-- tokopt:begin v1 -->\nglobal\n<!-- tokopt:end -->\n")
        seen = self.tmp / "seen"
        code, _, err = self.bench("r", "--rules", str(rules), seen=seen)
        self.assertEqual(code, 0)
        self.assertIn("<!-- tokopt:begin", seen.read_text())
        self.assertIn("Be brief.", seen.read_text())
        self.assertIn("global CLAUDE.md already has a tokopt rules block", err)
        r = self.results("r")[0]
        self.assertEqual((r["rules"], r["global_rules"]), (str(rules), True))

    def test_a_broken_agent_command_is_reported(self):  # not silently recorded as a free run
        spec = json.loads(self.tasks(repeat=1, n_tasks=1).read_text())
        spec["agent_cmd"] = [sys.executable, "-c", "import sys; sys.exit(3)"]
        self.tasks_path = self.tmp / "broken.json"
        self.tasks_path.write_text(json.dumps(spec))
        code, out, _ = self.bench("broken")
        self.assertEqual(code, 0)
        self.assertIn("agent exited 3, no usage", out)
        self.bench("broken2")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            cli.main(["compare", "broken", "broken2"])
        self.assertIn("warning: 1 + 1 runs have no usage", out.getvalue())

    def test_bad_tasks_file(self):
        bad = self.tmp / "bad.json"
        bad.write_text('{"tasks": []}')
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["bench", str(bad), "--label", "x"]), 2)
        self.assertIn("tokopt bench:", err.getvalue())


class TestCompare(BenchCase):
    def setUp(self):
        super().setUp()
        self.tasks_path = self.tasks(repeat=4, n_tasks=3)

    def test_accept_when_b_is_30_percent_cheaper(self):
        self.bench("base", noise=0.05)
        self.bench("cheap", scale=0.7, noise=0.05)
        d = self.compare("base", "cheap")
        self.assertEqual(d["verdict"], "ACCEPT")
        self.assertAlmostEqual(d["change"], -0.3, delta=0.05)
        self.assertLess(d["ci"][1], 0)
        self.assertEqual(d["pass_rate"], {"a": 1.0, "b": 1.0})
        self.assertEqual(len(d["tasks"]), 3)

    def test_reject_when_pass_rate_drops(self):
        self.bench("base", noise=0.05)
        self.bench("broken", scale=0.5, noise=0.05, **{"pass": "half"})
        self.assertEqual(self.compare("base", "broken")["verdict"], "REJECT")

    def test_reject_when_not_cheaper(self):
        self.bench("base", noise=0.05)
        self.bench("dear", scale=1.2, noise=0.05)
        self.assertEqual(self.compare("base", "dear")["verdict"], "REJECT")

    def test_inconclusive_within_noise(self):
        self.bench("base", noise=0.3)
        self.bench("same", scale=0.97, noise=0.3)
        self.assertEqual(self.compare("base", "same")["verdict"], "INCONCLUSIVE")

    def test_output_verdict(self):  # N1.5c: a rules variant wins on output
        self.bench("v1", noise=0.05)
        self.bench("terse", output_scale=0.7, noise=0.05)
        self.bench("meh", output_scale=0.95, noise=0.05)
        self.assertEqual(self.compare("v1", "terse")["output"]["verdict"], "ACCEPT")
        # 5 % less is still less: not a win (< 10 %), not a REJECT either (A6.3)
        self.assertEqual(self.compare("v1", "meh")["output"]["verdict"], "INCONCLUSIVE")

    def test_only_common_tasks(self):
        self.bench("base", noise=0.05)
        self.tasks_path = self.tasks(repeat=4, n_tasks=2)
        self.bench("two", scale=0.7, noise=0.05)
        self.assertEqual(sorted(t["task"] for t in self.compare("base", "two")["tasks"]), ["t0", "t1"])

    def test_text_output_and_unknown_label(self):
        self.bench("base", noise=0.05)
        self.bench("cheap", scale=0.7, noise=0.05)
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["compare", "base", "cheap"])
        self.assertIn("verdict: ACCEPT", out.getvalue())
        self.assertIn("median output tokens", out.getvalue())
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["compare", "base", "nope"]), 1)
        self.assertIn("no bench results for 'nope'", err.getvalue())


if __name__ == "__main__":
    unittest.main()
