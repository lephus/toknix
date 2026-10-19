"""P4-T5: `toknix ui`, the read-only localhost page (A6.7)."""
from __future__ import annotations

import http.client
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from toknix import cli, runner, store, ui

T0 = 1_800_000_000.0
SAVED = {"default": 4000, "hex": 200, "non_ascii": 30}  # 1000 + 100 + 20 tokens with the default ratios


class TempHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TOKNIX_") and k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        patcher = mock.patch.dict(os.environ, dict(env, TOKNIX_HOME=tmp.name, HOME=tmp.name, USERPROFILE=tmp.name),
                                  clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_row(self, cmd="pytest", tee_id="", at=T0, kind="pytest"):
        with mock.patch.object(store.time, "time", return_value=at):
            store.record_run(kind, cmd, 1, 10_000, 5_000, 200, 40, tee_id, 0, SAVED)

    def guard_row(self, rule="junk", path="dist/app.js", at=T0, est=300):
        with mock.patch.object(store.time, "time", return_value=at):
            store.record_guard("s1", rule, path, est)

    def tee(self, tee_id: str, raw: bytes, filtered: str | None = None):
        d = self.home / "tee"
        d.mkdir(exist_ok=True)
        (d / f"{tee_id}.log").write_bytes(raw)
        if filtered is not None:
            (d / f"{tee_id}.out.log").write_text(filtered, encoding="utf-8")


class Served(TempHome):
    """A real server on a free port, in a thread."""

    def setUp(self):
        super().setUp()
        self.server = ui.make_server(0)
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path: str, host: str | None = None) -> tuple[int, dict, bytes]:
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            con.putrequest("GET", path, skip_host=True)
            con.putheader("Host", host or f"127.0.0.1:{self.port}")
            con.endheaders()
            r = con.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            con.close()

    def get_json(self, path: str):
        status, headers, body = self.get(path)
        self.assertEqual(status, 200, body[:300])
        self.assertTrue(headers["content-type"].startswith("application/json"))
        return json.loads(body)


class TestFilteredOutputIsKept(TempHome):
    def test_a_teed_run_saves_what_the_agent_got(self):
        raw = "".join(f"tests/test_{i}.py::test_ok PASSED\n" for i in range(400)).encode()
        body = runner.filter_output("pytest", raw, 0)
        tee_id = [r for r in self.rows() if r][0][0]
        self.assertTrue(tee_id)
        self.assertEqual((self.home / "tee" / f"{tee_id}.out.log").read_text(encoding="utf-8"), body)
        self.assertEqual((self.home / "tee" / f"{tee_id}.log").read_bytes(), raw)

    def test_a_small_run_keeps_nothing(self):
        runner.filter_output("pytest", b"1 passed\n", 0)
        self.assertEqual(list((self.home / "tee").glob("*")) if (self.home / "tee").exists() else [], [])

    def test_the_filtered_file_is_pruned_with_the_tee(self):
        self.tee("abc123", b"x\n", "y\n")
        old = T0 - 30 * 86400
        for f in (self.home / "tee").glob("*"):
            os.utime(f, (old, old))
        with mock.patch.object(runner.time, "time", return_value=T0):
            runner.prune_tee(7)
        self.assertEqual(list((self.home / "tee").glob("*")), [])

    def rows(self):
        with store.db() as c:
            return c.execute("SELECT tee_id FROM runs").fetchall()


class TestEvents(TempHome):
    def test_runs_and_guards_merged_newest_first(self):
        self.run_row("pytest -q", "aa11", at=T0)
        self.guard_row("bigfile", "src/big.py", at=T0 + 10, est=900)
        self.run_row("git status", at=T0 + 20, kind="git")
        self.tee("aa11", b"raw\n", "out\n")
        ev = ui.events()
        self.assertEqual([e["type"] for e in ev], ["run", "guard", "run"])
        self.assertEqual([e["ts"] for e in ev], [T0 + 20, T0 + 10, T0])
        run = ev[2]
        self.assertEqual((run["cmd"], run["kind"], run["exit"], run["tee_id"]), ("pytest -q", "pytest", 1, "aa11"))
        self.assertEqual((run["raw_lines"], run["out_lines"], run["raw_chars"], run["out_chars"]),
                         (200, 40, 10_000, 5_000))
        self.assertEqual(run["saved_tokens"], 1120)
        self.assertTrue(run["has_output"])
        self.assertFalse(ev[0]["has_output"])  # no tee
        self.assertEqual((ev[1]["rule"], ev[1]["path"], ev[1]["est_tokens"], ev[1]["session"]),
                         ("bigfile", "src/big.py", 900, "s1"))

    def test_a_pruned_tee_has_no_output(self):
        self.run_row("pytest", "bb22")
        self.assertFalse(ui.events()[0]["has_output"])

    def test_recalled_flag(self):
        self.run_row("pytest", "cc33")
        store.record_recall("cc33")
        self.assertTrue(ui.events()[0]["recalled"])

    def test_limit_and_before_page_back(self):
        for i in range(5):
            self.run_row(f"cmd{i}", at=T0 + i)
        self.assertEqual([e["cmd"] for e in ui.events(limit=2)], ["cmd4", "cmd3"])
        self.assertEqual([e["cmd"] for e in ui.events(limit=2, before=T0 + 3)], ["cmd2", "cmd1"])

    def test_no_db_is_empty_and_creates_nothing(self):
        self.assertEqual(ui.events(), [])
        self.assertEqual(ui.daily(), [])
        self.assertFalse((self.home / "toknix.db").exists())


class TestDaily(TempHome):
    def test_tokens_per_local_day_split_by_source(self):
        self.run_row(at=T0)
        self.run_row(at=T0 + 60)
        self.guard_row(at=T0 + 120, est=300)
        self.run_row(at=T0 - 86400)
        self.run_row(at=T0 - 40 * 86400)  # outside the window
        days = ui.daily(days=14, now=T0 + 200)
        self.assertEqual(len(days), 14)
        self.assertEqual(days[-1]["filter_tokens"], 2240)
        self.assertEqual(days[-1]["guard_tokens"], 300)
        self.assertEqual(days[-2]["filter_tokens"], 1120)
        self.assertEqual(sum(d["filter_tokens"] for d in days[:-2]), 0)
        self.assertLess(days[0]["day"], days[-1]["day"])


class TestOutput(TempHome):
    def test_original_and_filtered(self):
        self.tee("abc123", "a\nb\n".encode(), "a\n[toknix: …]\n")
        o = ui.output("abc123")
        self.assertEqual((o["original"], o["filtered"]), ("a\nb\n", "a\n[toknix: …]\n"))
        self.assertFalse(o["original_truncated"] or o["filtered_truncated"])

    def test_old_run_without_filtered_file(self):
        self.tee("abc123", b"a\n")
        self.assertIsNone(ui.output("abc123")["filtered"])

    def test_bad_or_unknown_ids(self):
        self.tee("abc123", b"a\n")
        for bad in ("../toknix.db", "ABC123", "abc123.out", "", "zz", "abc124"):
            with self.subTest(bad=bad):
                self.assertIsNone(ui.output(bad))

    def test_long_output_is_cut_with_a_flag(self):
        self.tee("abc123", b"x" * 50, "y" * 10)
        with mock.patch.object(ui, "load", return_value=dict(ui.load(), ui_max_output_chars=20)):
            o = ui.output("abc123")
        self.assertEqual((len(o["original"]), o["original_truncated"]), (20, True))
        self.assertEqual((o["filtered"], o["filtered_truncated"]), ("y" * 10, False))

    def test_viewing_is_not_a_recall(self):
        self.run_row("pytest", "abc123")
        self.tee("abc123", b"a\n")
        ui.output("abc123")
        with store.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM recalls").fetchone()[0], 0)


class TestServer(Served):
    def test_page(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertIn(b"toknix", body)
        self.assertNotIn(b"http://", body.replace(b"http://127.0.0.1", b""))  # nothing loaded from outside
        self.assertNotIn(b"https://", body)

    def test_api(self):
        self.run_row("pytest", "abc123", at=T0)
        self.tee("abc123", b"raw\n", "out\n")
        self.assertEqual([e["cmd"] for e in self.get_json("/api/events?limit=5")], ["pytest"])
        self.assertEqual(len(self.get_json("/api/daily?days=3")), 3)
        self.assertEqual(self.get_json("/api/output/abc123")["filtered"], "out\n")
        self.assertEqual(self.get_json("/api/summary")["filter_tokens"], 1120)

    def test_not_found(self):
        self.run_row()
        for path in ("/api/output/..%2Ftoknix.db", "/api/output/abc999", "/nope", "/api/events/x"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 404)

    def test_bad_query_is_400(self):
        self.run_row()
        for path in ("/api/events?limit=x", "/api/events?limit=0", "/api/daily?days=-1", "/api/summary?days=a"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 400)

    def test_limits_are_capped(self):
        for i in range(3):
            self.run_row(f"c{i}", at=T0 + i)
        self.assertEqual(len(self.get_json("/api/events?limit=99999")), 3)
        self.assertEqual(len(self.get_json("/api/daily?days=99999")), 90)

    def test_foreign_host_is_403(self):  # DNS rebinding
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1:1", f"localhost.evil:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.get("/api/events", host=host)[0], 403)
        self.assertEqual(self.get("/api/events", host=f"localhost:{self.port}")[0], 200)

    def test_post_is_refused(self):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            con.request("POST", "/api/events", body=b"{}")
            self.assertNotEqual(con.getresponse().status, 200)
        finally:
            con.close()

    def test_no_db_is_not_created(self):
        self.assertEqual(self.get_json("/api/events"), [])
        self.assertTrue(self.get_json("/api/summary")["no_data"])
        self.assertFalse((self.home / "toknix.db").exists())

    def test_listens_on_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


class TestPorts(TempHome):
    def busy_port(self) -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        self.addCleanup(s.close)
        return s.getsockname()[1]

    def test_busy_default_port_falls_back(self):
        port = self.busy_port()
        with mock.patch.object(ui, "DEFAULT_PORT", port):
            server = ui.make_server(None)
        self.addCleanup(server.server_close)
        self.assertNotEqual(server.server_address[1], port)

    def test_busy_chosen_port_is_an_error(self):
        port = self.busy_port()
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = cli.main(["ui", "--port", str(port), "--no-open"])
        self.assertEqual(code, 1)
        self.assertIn(str(port), err.getvalue())

    def test_cli_prints_the_url_and_stops_on_ctrl_c(self):
        out = io.StringIO()
        with mock.patch.object(ui.ThreadingHTTPServer, "serve_forever", side_effect=KeyboardInterrupt), \
                mock.patch.object(ui.webbrowser, "open") as opened, redirect_stdout(out):
            code = cli.main(["ui", "--port", "0", "--no-open"])
        self.assertEqual(code, 0)
        self.assertRegex(out.getvalue(), r"http://127\.0\.0\.1:\d+/")
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
