"""P10-T1: `toknix hook agent`, a cheaper model for subagents by task (A9)."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from toknix import hooks
from transcript_factory import ClaudeTranscript, text


class AgentHook(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / ".toknix"
        self.claude = self.tmp / ".claude"
        self.cwd = self.tmp / "proj"
        self.cwd.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("TOKNIX_")}
        patcher = mock.patch.dict(os.environ, dict(env, TOKNIX_HOME=str(self.home),
                                                   CLAUDE_CONFIG_DIR=str(self.claude)), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = self.tmp / "s1.jsonl"
        self.main("claude-opus-5-5")

    def main(self, model: str):
        t = ClaudeTranscript(model=model)
        t.assistant([text("a")], input=0, cache_read=50000, output=1)
        t.write(self.path)

    def config(self, **kv):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.json").write_text(json.dumps(kv))

    def reply(self, description="Implement Task 3", prompt="You are implementing Task 3: add the parser.",
              tool="Agent", **ti) -> dict | None:
        tool_input = dict({"description": description, "prompt": prompt, "subagent_type": "general-purpose"}, **ti)
        return hooks.hook_agent({"session_id": "s1", "transcript_path": str(self.path), "cwd": str(self.cwd),
                                 "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input})

    def model(self, **kw) -> str | None:
        r = self.reply(**kw)
        return r["hookSpecificOutput"]["updatedInput"]["model"] if r else None

    def test_routes_by_task(self):
        for kw, want in [
            (dict(description="Implement Task 3"), "sonnet"),
            (dict(description="Fix the parser bug"), "sonnet"),
            (dict(description="Review spec compliance"), "sonnet"),
            (dict(description="Research model routing"), "sonnet"),
            (dict(description="Find callers of load"), "sonnet"),
            (dict(description="Tìm chỗ gọi hàm load"), "sonnet"),
            (dict(description="Explore auth flow", subagent_type="Explore"), "haiku"),
            (dict(description="Design the cache layer"), None),
            (dict(description="Write implementation plan"), None),
            (dict(description="Debug flaky test"), None),
            (dict(description="Root cause of the crash"), None),
            (dict(description="Brainstorm ideas"), None),
            (dict(description="Thiết kế lớp cache"), None),
            (dict(description="Do the thing", prompt="Something vague."), None),
        ]:
            with self.subTest(**kw):
                self.assertEqual(self.model(**kw), want)

    def test_description_wins_over_prompt(self):
        # an implementer's prompt mentions the plan and debugging further down; the description decides
        self.assertEqual(self.model(description="Implement Task 2",
                                    prompt="Read the plan. If a test fails, debug it."), "sonnet")
        # no class in the description: the start of the prompt decides
        self.assertEqual(self.model(description="Task helper", prompt="Search the repo for uses of load()."),
                         "sonnet")
        self.assertIsNone(self.model(description="Helper", prompt="x" * 700 + " search the repo"))

    def test_keeps_the_rest_of_the_input(self):
        r = self.reply(run_in_background=True)
        self.assertEqual(r, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {
            "description": "Implement Task 3", "prompt": "You are implementing Task 3: add the parser.",
            "subagent_type": "general-purpose", "run_in_background": True, "model": "sonnet"}}})

    def test_missing_subagent_type_is_general_purpose(self):
        r = hooks.hook_agent({"session_id": "s1", "transcript_path": str(self.path), "cwd": str(self.cwd),
                              "tool_name": "Task", "tool_input": {"description": "Implement Task 1", "prompt": "go"}})
        self.assertEqual(r["hookSpecificOutput"]["updatedInput"]["model"], "sonnet")

    def test_model_already_chosen(self):
        self.assertIsNone(self.reply(model="opus"))
        self.assertEqual(self.model(model=""), "sonnet")

    def test_other_agent_types_are_left_alone(self):
        for t in ("fork", "Plan", "code-reviewer", "superpowers:code-reviewer"):
            with self.subTest(t=t):
                self.assertIsNone(self.reply(subagent_type=t))

    def test_user_definition_of_the_agent_decides(self):
        for d in (self.cwd / ".claude" / "agents", self.claude / "agents"):
            with self.subTest(d=d):
                d.mkdir(parents=True)
                (d / "Explore.md").write_text("---\nname: Explore\n---\n")
                self.assertIsNone(self.reply(description="Explore x", subagent_type="Explore"))
                (d / "Explore.md").unlink()
                d.rmdir()

    def test_never_routes_up(self):
        self.main("claude-sonnet-5-5")
        self.assertIsNone(self.reply())  # sonnet → sonnet: nothing to gain
        self.assertEqual(self.model(description="Explore x", subagent_type="Explore"), "haiku")
        self.main("claude-haiku-5-5")
        self.assertIsNone(self.reply())
        self.assertIsNone(self.reply(description="Explore x", subagent_type="Explore"))

    def test_unknown_main_model_is_left_alone(self):
        self.main("gpt-5-codex")
        self.assertIsNone(self.reply())
        self.path.unlink()
        self.assertIsNone(self.reply())

    def test_main_model_ignores_subagent_lines(self):
        t = ClaudeTranscript(model="claude-opus-5-5")
        t.assistant([text("a")], input=0, cache_read=50000, output=1)
        t.assistant([text("s")], input=0, cache_read=100, output=1, sidechain=True, model="claude-haiku-5-5")
        t.write(self.path)
        self.assertEqual(self.model(), "sonnet")

    def test_first_response_reads_the_request_line(self):
        # On the first response the assistant line is not written yet when the hook runs, but the request is:
        # `{"type":"api-request", "params":{"model":…}}` (Claude Code 2.1.308). It is also the current model
        # right after /model.
        def request(model):
            return json.dumps({"type": "api-request", "querySource": "repl_main_thread", "params": {"model": model}})
        self.path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n"
                             + request("claude-opus-5-5") + "\n")
        self.assertEqual(self.model(), "sonnet")
        self.main("claude-opus-5-5")
        with self.path.open("a") as fh:
            fh.write(request("claude-sonnet-5-5") + "\n")
        self.assertIsNone(self.reply())

    def test_config(self):
        self.config(route_subagents=False)
        self.assertIsNone(self.reply())
        self.config(route_models={"implement": None, "read": "haiku"})
        self.assertIsNone(self.reply())
        self.assertEqual(self.model(description="Review the diff"), "haiku")
        self.assertEqual(self.model(description="Explore", subagent_type="Explore"), "haiku")  # default kept
        self.config(route_agent_types=["Explore"])
        self.assertIsNone(self.reply())
        self.config(route_models={"implement": "claude-sonnet-5-5"})
        self.assertEqual(self.model(), "claude-sonnet-5-5")
        self.config(route_models={"implement": "mystery"})  # no price: cannot tell it is cheaper
        self.assertIsNone(self.reply())
        # a partial model_aliases adds to the defaults, like route_models
        self.config(model_aliases={"sonnet": "claude-sonnet-5-5"})
        self.assertEqual(self.model(description="Explore", subagent_type="Explore"), "haiku")
        self.config(model_aliases={"haiku": "claude-opus-5-5"})  # an alias that is no cheaper is left alone
        self.assertIsNone(self.reply(description="Explore", subagent_type="Explore"))

    def test_hook_permission(self):
        self.config(hook_permission="ask")
        self.assertEqual(self.reply()["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_bad_input_is_no_opinion(self):
        for data in ({}, {"tool_name": "Agent"}, {"tool_name": "Agent", "tool_input": "x"},
                     {"tool_name": "Bash", "tool_input": {"command": "ls"}},
                     {"tool_name": "Agent", "tool_input": {"description": 3, "prompt": None}}):
            with self.subTest(data=data):
                self.assertIsNone(hooks.hook_agent(data))

    def test_records_the_route(self):
        self.reply()
        self.reply(description="Design it")
        con = sqlite3.connect(str(self.home / "toknix.db"))
        try:  # closed before the temp dir is removed: Windows cannot delete an open database
            rows = con.execute("SELECT session, agent_type, kind, main_model, model FROM routes").fetchall()
        finally:
            con.close()
        self.assertEqual(rows, [("s1", "general-purpose", "implement", "claude-opus-5-5", "sonnet")])

    def test_main_entry_point_prints_json(self):
        import io
        data = {"session_id": "s1", "transcript_path": str(self.path), "cwd": str(self.cwd), "tool_name": "Agent",
                "tool_input": {"description": "Implement Task 3", "prompt": "go", "subagent_type": "general-purpose"}}
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(data))), mock.patch("sys.stdout", out):
            self.assertEqual(hooks.main("agent"), 0)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["updatedInput"]["model"], "sonnet")


if __name__ == "__main__":
    unittest.main()
