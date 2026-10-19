"""P3-T4: `toknix map` (A5.2)."""
from __future__ import annotations

import datetime
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support

from toknix import cli, codesearch
from toknix.textutil import est_tokens

MAP_REPO = support.FIXTURES / "map_repo"
# Hand-computed from tests/fixtures/map_repo (ref_count = other files holding the name as a word):
#   app/core.py   Config 4 (cli, jobs, report, server.ts), parse 4  → log 5 + log 5 ≈ 3.22
#   app/util.py   slugify 2 (cli, jobs)                              → log 3 ≈ 1.10
#   all others    main, run_jobs, lonely, build_report, startServer 0 → 0, then by path
EXPECTED_ORDER = ["app/core.py", "app/util.py", "app/cli.py", "app/jobs.py", "app/orphan.py", "app/report.py",
                  "web/server.ts"]


class MapCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, {"TOKNIX_HOME": str(self.tmp / ".toknix")})
        patcher.start()
        self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)

    def map(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["map", *argv])
        return code, out.getvalue(), err.getvalue()


class TestRanking(MapCase):
    def test_known_ranking(self):
        os.chdir(MAP_REPO)
        code, out, err = self.map()
        self.assertEqual((code, err), (0, ""))
        headers = [l for l in out.splitlines() if l and not l.startswith((" ", "["))]
        self.assertEqual(headers, EXPECTED_ORDER)

    def test_scores(self):
        scores = {r.path.relative_to(MAP_REPO).as_posix(): round(r.score, 2)
                  for r in codesearch.rank_files(codesearch.source_files(MAP_REPO))}
        self.assertEqual(scores["app/core.py"], 3.22)
        self.assertEqual(scores["app/util.py"], 1.10)
        self.assertEqual(scores["app/orphan.py"], 0.0)

    def test_names_defined_in_several_files_share_their_score(self):
        # Item is defined twice and used by 4 files: whose Item is used is unknown, so each definer gets
        # half; Engine is defined once and used by 2 files. Unsplit, a.py and b.py would come first.
        root = self.tmp / "dup"
        files = {"a.py": "class Item:\n    pass\n", "b.py": "class Item:\n    pass\n",
                 "c.py": "class Engine:\n    pass\n",
                 **{f"u{i}.py": f"from x import Item\ndef use{i}():\n    return Item()\n" for i in range(4)},
                 **{f"e{i}.py": f"from c import Engine\ndef run{i}():\n    return Engine()\n" for i in range(2)}}
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        order = [r.path.name for r in codesearch.rank_files(codesearch.source_files(root))]
        self.assertEqual(order[:3], ["c.py", "a.py", "b.py"])

    def test_file_block(self):
        os.chdir(MAP_REPO)
        lines = self.map()[1].splitlines()
        i = lines.index("app/core.py")
        self.assertEqual(lines[i + 1:i + 3], ["  L4  class Config", "  L9  def parse(text)"])  # top-level only

    def test_dir_argument_and_relative_paths(self):
        os.chdir(support.ROOT)
        out = self.map(str(MAP_REPO.relative_to(support.ROOT)))[1]
        self.assertIn("tests/fixtures/map_repo/app/core.py\n", out)


class TestBudget(MapCase):
    def test_never_over_budget(self):  # plan: not more than 5 % over; we stay at or under
        os.chdir(support.ROOT)
        for budget in (60, 200, 500, 1500, 4000):
            with self.subTest(budget=budget):
                code, out, _ = self.map("--budget", str(budget))
                self.assertEqual(code, 0)
                self.assertLessEqual(est_tokens(out), budget)
                if "more with --budget" in out and budget >= 200:  # cut short: it should use most of the budget
                    self.assertGreater(est_tokens(out), budget * 0.8)

    def test_footer_says_what_was_left_out(self):
        os.chdir(support.ROOT)
        out = self.map("--budget", "300")[1].splitlines()
        self.assertRegex(out[-1], r"^\[map: \d+ of \d+ files, ~\d+ tokens; more with --budget \d+\]$")

    def test_everything_fits(self):
        os.chdir(MAP_REPO)
        out = self.map("--budget", "5000")[1].splitlines()
        self.assertRegex(out[-1], r"^\[map: 7 of 7 files, ~\d+ tokens\]$")

    def test_bad_budget(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            cli.main(["map", "--budget", "0"])
        self.assertEqual(e.exception.code, 2)


class TestLargeRepo(MapCase):
    """Repo > map_max_files: only files changed in the last map_recent_days days (A5.2 [ASSUMPTION])."""

    def test_only_recent_files(self):
        root = self.tmp / "repo"
        root.mkdir()
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")

        def commit(date: str):
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "x", "--date", date], check=True,
                           env=dict(env, GIT_COMMITTER_DATE=date))

        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        for i in range(6):
            (root / f"old{i}.py").write_text(f"def old_{i}():\n    pass\n")
        commit("2020-01-01T00:00:00")
        (root / "new.py").write_text("def fresh():\n    pass\n")
        commit(datetime.datetime.now().replace(microsecond=0).isoformat())
        (self.tmp / ".toknix").mkdir()
        (self.tmp / ".toknix" / "config.json").write_text('{"map_max_files": 5}')
        os.chdir(root)
        out = self.map()[1]
        self.assertIn("new.py", out)
        self.assertNotIn("old0.py", out)
        self.assertIn("only files changed in the last 90 days: 1 of 7", out)

    def test_small_repo_is_whole(self):
        os.chdir(MAP_REPO)
        self.assertNotIn("only files changed", self.map()[1])


if __name__ == "__main__":
    unittest.main()
