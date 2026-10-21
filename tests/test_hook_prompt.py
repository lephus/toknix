"""P7-T2: `toknix hook prompt`, the context budget reminder (A7)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support

from toknix import hooks
from transcript_factory import ClaudeTranscript, text


class PromptHook(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / ".toknix"
        env = {k: v for k, v in os.environ.items() if not k.startswith("TOKNIX_")}
        patcher = mock.patch.dict(os.environ, dict(env, TOKNIX_HOME=str(self.home)), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = self.tmp / "s1.jsonl"

    def transcript(self, *contexts: int, sidechain_after: int = 0) -> ClaudeTranscript:
        t = ClaudeTranscript()
        for ctx in contexts:
            t.assistant([text("a")], input=0, cache_read=ctx, output=1)
        if sidechain_after:
            t.assistant([text("sub")], input=0, cache_read=sidechain_after, output=1, sidechain=True)
        t.write(self.path)
        return t

    def reply(self, prompt: str = "next task") -> dict | None:
        return hooks.hook_prompt({"session_id": "s1", "transcript_path": str(self.path), "prompt": prompt,
                                  "hook_event_name": "UserPromptSubmit"})

    def test_over_budget_tells_the_user(self):
        self.transcript(50000, 280000)
        self.assertEqual(self.reply(), {"systemMessage": (
            "toknix: context 280k is over the 200k budget; every request re-reads it (~14.0k ETU, $0.056 each). "
            "Start the next task with /clear, or /compact.")})

    def test_cost_follows_the_last_requests_model(self):  # P8-T2 (A1)
        for model, each in (("claude-sonnet-5-5", "~7.0k ETU, $0.028 each"), ("gpt-5-codex", "~28.0k ETU each")):
            with self.subTest(model=model):
                t = ClaudeTranscript(model=model)
                t.assistant([text("a")], input=0, cache_read=280000, output=1)
                t.write(self.path)
                self.assertIn(f"re-reads it ({each})", self.reply()["systemMessage"])

    def test_under_budget_is_silent(self):
        self.transcript(280000, 199999)  # the last main request counts, not the biggest
        self.assertIsNone(self.reply())

    def test_subagent_requests_do_not_count(self):
        self.transcript(1000, sidechain_after=500000)
        self.assertIsNone(self.reply())

    def test_clear_and_compact_are_silent(self):
        self.transcript(280000)
        for prompt in ("/clear", "/compact keep the plan", "  /compact"):
            with self.subTest(prompt=prompt):
                self.assertIsNone(self.reply(prompt))

    def test_budget_from_config(self):
        self.home.mkdir(parents=True)
        (self.home / "config.json").write_text(json.dumps({"context_budget": 5000}))
        self.transcript(6000)
        self.assertIn("over the 5.0k budget", self.reply()["systemMessage"])

    def test_huge_line_before_the_last_request(self):  # only the tail is read: real transcripts reach 69 MB
        t = self.transcript(280000)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(("{\"type\": \"user\", \"message\": {\"content\": \"" + "x" * 2_000_000 + "\"}}\n"))
            t.assistant([text("b")], input=0, cache_read=250000, output=1)
            f.write(t.lines[-1] + "\n")
        self.assertIn("context 250k", self.reply()["systemMessage"])

    def test_cli_and_bad_input(self):
        self.transcript(280000)
        env = dict(os.environ, PYTHONPATH=str(support.SRC), PYTHONDONTWRITEBYTECODE="1")

        def run(stdin: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-m", "toknix", "hook", "prompt"], input=stdin.encode(),
                                  capture_output=True, env=env, timeout=30)

        p = run(json.dumps({"session_id": "s1", "transcript_path": str(self.path), "prompt": "go"}))
        self.assertEqual(p.returncode, 0)
        self.assertIn("over the 200k budget", json.loads(p.stdout)["systemMessage"])
        for stdin in ("", "nope", "[]", json.dumps({"transcript_path": str(self.tmp / "missing.jsonl")}),
                      json.dumps({"transcript_path": 5, "prompt": None})):
            with self.subTest(stdin=stdin):
                p = run(stdin)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))


if __name__ == "__main__":
    unittest.main()
