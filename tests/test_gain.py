"""P1-T5: `toknix gain` (A6.2, N1.1, N2.1)."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from toknix import cli, runner, store
from toknix.textutil import char_classes

# 4000 default + 200 hex + 30 non-ASCII chars → 1000 + 100 + 20 tokens with the default ratios
SAVED = {"default": 4000, "hex": 200, "non_ascii": 30}
SAVED_TOKENS = 1120


class TempHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TOKNIX_") and k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        # HOME too: gain reads transcripts (R̄), and the real ~/.claude must never leak into a test
        patcher = mock.patch.dict(os.environ, dict(env, TOKNIX_HOME=tmp.name, HOME=tmp.name, USERPROFILE=tmp.name),
                                  clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def gain(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["gain", *args])
        return code, out.getvalue(), err.getvalue()

    def gain_json(self, *args: str) -> dict:
        code, out, err = self.gain("--json", *args)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def run_row(self, kind="pytest", cmd="pytest", tee_id="", overflow=0, saved=SAVED, at=None):
        with mock.patch.object(store.time, "time", return_value=at or 1_800_000_000.0):
            store.record_run(kind, cmd, 1, 10_000, 5_000, 200, 40, tee_id, overflow, saved)


class TestNoData(TempHome):
    def test_no_db_says_no_data_and_creates_nothing(self):
        self.assertEqual(self.gain(), (0, "no data yet\n", ""))
        self.assertFalse((self.home / "toknix.db").exists())

    def test_empty_db_says_no_data(self):
        with store.db():
            pass
        self.assertEqual(self.gain()[1], "no data yet\n")

    def test_unreadable_db_is_reported(self):
        (self.home / "toknix.db").write_bytes(b"this is not sqlite" * 100)
        code, out, err = self.gain()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("toknix gain: cannot read", err)

    def test_json_without_data_is_still_json(self):
        data = self.gain_json()
        self.assertTrue(data["no_data"])
        self.assertEqual(data["direct_tokens"], 0)


class TestTotals(TempHome):
    def test_direct_savings_use_char_classes_and_ratios(self):
        self.run_row()
        self.run_row(kind="install", cmd="npm install")
        data = self.gain_json()
        self.assertFalse(data["no_data"])
        self.assertEqual(data["filter_tokens"], 2 * SAVED_TOKENS)
        self.assertEqual(data["direct_tokens"], 2 * SAVED_TOKENS)

    def test_ratios_come_from_config(self):
        (self.home / "config.json").write_text(json.dumps({"token_ratios": {"default": 2.0}}))
        self.run_row()
        self.assertEqual(self.gain_json()["filter_tokens"], 2000 + 100 + 20)

    def test_rows_without_char_classes_fall_back_to_default_ratio(self):
        self.run_row(saved=None)  # 10 000 − 5 000 chars / 4
        self.assertEqual(self.gain_json()["filter_tokens"], 1250)

    def test_guard_savings_by_rule(self):
        store.record_guard("s1", "junk", "node_modules/x.js", 900)
        store.record_guard("s1", "junk", "package-lock.json", 100)
        store.record_guard("s2", "bigfile", "big.py", 3000)
        data = self.gain_json()
        self.assertEqual(data["guard_tokens"], 4000)
        self.assertEqual(data["direct_tokens"], 4000)
        self.assertEqual(data["rules"], [{"rule": "bigfile", "denials": 1, "saved_tokens": 3000},
                                         {"rule": "junk", "denials": 2, "saved_tokens": 1000}])

    def test_reread_estimate_uses_weights_and_default_r(self):
        self.run_row()
        r = self.gain_json()["reread"]
        self.assertEqual(r, {"etu": SAVED_TOKENS * 3.25, "multiplier": 3.25, "r_bar": 20, "r_bar_source": "default"})

    def test_reread_follows_config(self):
        (self.home / "config.json").write_text(json.dumps({"weights": [1, 2.0, 0.5, 5], "reread_default": 10}))
        self.run_row()
        self.assertEqual(self.gain_json()["reread"]["multiplier"], 2.0 + 0.5 * 10)

    def test_days_window(self):
        self.run_row(at=1_000.0)  # long ago
        self.run_row()
        with mock.patch("toknix.measure.time.time", return_value=1_800_000_000.0 + 60):
            self.assertEqual(self.gain_json("--days", "7")["filter_tokens"], SAVED_TOKENS)
            self.assertEqual(self.gain_json()["filter_tokens"], 2 * SAVED_TOKENS)


class TestKinds(TempHome):
    def test_cuts_recalls_and_rate(self):
        for i in range(4):
            self.run_row(kind="pytest", tee_id=f"a{i}")
        self.run_row(kind="pytest")  # not cut
        self.run_row(kind="passthrough", saved={"default": 0, "hex": 0, "non_ascii": 0})
        store.record_recall("a0")
        store.record_recall("a0")  # same tee twice counts once
        store.record_recall("a1")
        kinds = {k["kind"]: k for k in self.gain_json()["kinds"]}
        self.assertEqual(kinds["pytest"], {"kind": "pytest", "runs": 5, "cuts": 4, "recalled": 2,
                                           "recall_rate": 0.5, "error_overflow": 0,
                                           "saved_tokens": 5 * SAVED_TOKENS, "warn": False})
        self.assertEqual(kinds["passthrough"]["saved_tokens"], 0)
        self.assertEqual(kinds["passthrough"]["recall_rate"], None)

    def test_warns_over_20_percent_with_at_least_10_cuts(self):
        for i in range(10):
            self.run_row(kind="jest", tee_id=f"j{i}")
        for i in range(3):
            store.record_recall(f"j{i}")
        for i in range(9):
            self.run_row(kind="lint", tee_id=f"l{i}")
            store.record_recall(f"l{i}")  # 100 %, but only 9 cuts
        kinds = {k["kind"]: k for k in self.gain_json()["kinds"]}
        self.assertTrue(kinds["jest"]["warn"])
        self.assertFalse(kinds["lint"]["warn"])
        text = self.gain()[1]
        self.assertIn("filter 'jest' may cut too much: 3 of 10 cut outputs were recalled (30%)", text)
        self.assertNotIn("filter 'lint' may cut", text)

    def test_exactly_20_percent_does_not_warn(self):
        for i in range(10):
            self.run_row(kind="jest", tee_id=f"j{i}")
        store.record_recall("j0")
        store.record_recall("j1")
        self.assertFalse(self.gain_json()["kinds"][0]["warn"])

    def test_error_overflow_count(self):
        self.run_row(overflow=1)
        self.run_row(overflow=1, kind="generic")
        self.run_row()
        data = self.gain_json()
        self.assertEqual(data["error_overflow"], 2)
        self.assertEqual({k["kind"]: k["error_overflow"] for k in data["kinds"]}, {"pytest": 1, "generic": 1})


class TestTopCommands(TempHome):
    def test_grouped_sorted_and_capped_at_10(self):
        for i in range(12):
            for _ in range(i % 3 + 1):
                self.run_row(cmd=f"cmd {i}", saved={"default": 400 * (i + 1), "hex": 0, "non_ascii": 0})
        top = self.gain_json()["top_commands"]
        self.assertEqual(len(top), 10)
        self.assertEqual(top[0], {"cmd": "cmd 11", "runs": 3, "saved_tokens": 3 * 1200})
        self.assertEqual([t["saved_tokens"] for t in top], sorted((t["saved_tokens"] for t in top), reverse=True))

    def test_commands_that_saved_nothing_are_left_out(self):
        self.run_row(kind="passthrough", cmd="git status", saved={"default": 0, "hex": 0, "non_ascii": 0})
        self.assertEqual(self.gain_json()["top_commands"], [])


class TestText(TempHome):
    def test_report(self):
        self.run_row(tee_id="a0")
        store.record_guard("s1", "junk", "node_modules/x.js", 880)
        code, out, err = self.gain()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Saved directly: 2.0k tokens (filters 1.1k, read guard 880)", out)
        self.assertIn("With re-reads (estimate): 6.5k ETU", out)
        self.assertIn("R̄ = 20 is the default value, not measured", out)
        self.assertRegex(out, r"pytest\s+1\s+1\s+1\.1k\s+0\s+0%")
        self.assertRegex(out, r"junk\s+1\s+880")
        self.assertIn("pytest", out.split("Top commands")[1])

    def test_bad_days_is_a_usage_error(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as e:
                cli.main(["gain", "--days", "x"])
        self.assertEqual(e.exception.code, 2)


class TestRunnerRecordsCharClasses(TempHome):
    def test_saved_chars_per_class(self):
        sha = "0123456789abcdef" * 4
        text = "".join(f"PASSED test_{i} {sha} ✓\n" for i in range(60)) + "1 failed, 59 passed\n"
        done = subprocess.CompletedProcess([], 1, stdout=text.encode("utf-8"))
        out = io.StringIO()
        with mock.patch.object(runner.subprocess, "run", return_value=done), redirect_stdout(out):
            runner.run("pytest")
        raw, body = char_classes(text), char_classes(out.getvalue().rstrip("\n"))
        con = sqlite3.connect(str(self.home / "toknix.db"))
        row = con.execute("SELECT saved_default, saved_hex, saved_non_ascii FROM runs").fetchone()
        con.close()
        self.assertEqual(row, tuple(raw[k] - body[k] for k in ("default", "hex", "non_ascii")))
        self.assertGreater(row[1], 0)


class TestOldDatabase(TempHome):
    def test_runs_table_from_p1_t3b_gains_the_columns(self):
        con = sqlite3.connect(str(self.home / "toknix.db"))
        con.execute("CREATE TABLE runs(ts REAL, kind TEXT, cmd TEXT, exit INTEGER, raw_chars INTEGER, "
                    "out_chars INTEGER, raw_lines INTEGER, out_lines INTEGER, tee_id TEXT, "
                    "error_overflow INTEGER DEFAULT 0)")
        con.execute("INSERT INTO runs VALUES(1800000000, 'pytest', 'pytest', 1, 4000, 2000, 1, 1, '', 0)")
        con.commit()
        con.close()
        self.assertEqual(self.gain_json()["filter_tokens"], 500)  # (4000 − 2000) / 4


if __name__ == "__main__":
    unittest.main()
