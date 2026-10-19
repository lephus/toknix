"""`toknix ui` (A6.7): a read-only page on 127.0.0.1 showing what toknix filtered and blocked."""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from toknix import store
from toknix.config import load, toknix_home
from toknix.measure import _saved_tokens, gain_data, report_ratios
from toknix.runner import TEE_ID_RE

DEFAULT_PORT = 8765
PAGE = Path(__file__).resolve().parent / "templates" / "ui.html"
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
       "img-src 'self' data:; base-uri 'none'; form-action 'none'")


def _has_db() -> bool:
    return (toknix_home() / "toknix.db").exists()  # never create the db just to show it


def _tee(name: str) -> Path:
    return toknix_home() / "tee" / name


def events(limit: int = 200, before: float | None = None) -> list[dict]:
    """Runs and guard denials, newest first."""
    if not _has_db():
        return []
    ratios = report_ratios(load())
    before = 1e18 if before is None else before
    with store.db() as c:
        runs = c.execute("SELECT ts, kind, cmd, exit, raw_chars, out_chars, raw_lines, out_lines, tee_id, "
                         "error_overflow, saved_default, saved_hex, saved_non_ascii FROM runs WHERE ts < ? "
                         "ORDER BY ts DESC LIMIT ?", (before, limit)).fetchall()
        guards = c.execute("SELECT ts, session, rule, path, est_tokens FROM guard WHERE ts < ? "
                           "ORDER BY ts DESC LIMIT ?", (before, limit)).fetchall()
        recalled = {r[0] for r in c.execute("SELECT DISTINCT tee_id FROM recalls")}
    out = []
    for ts, kind, cmd, code, raw_chars, out_chars, raw_lines, out_lines, tee_id, overflow, *saved in runs:
        out.append({"type": "run", "ts": ts, "kind": kind, "cmd": cmd, "exit": code,
                    "raw_chars": raw_chars, "out_chars": out_chars, "raw_lines": raw_lines, "out_lines": out_lines,
                    "saved_tokens": round(_saved_tokens((raw_chars, out_chars, *saved), ratios)),
                    "tee_id": tee_id or "", "has_output": bool(tee_id) and _tee(f"{tee_id}.log").is_file(),
                    "recalled": bool(tee_id) and tee_id in recalled, "error_overflow": bool(overflow)})
    out += [{"type": "guard", "ts": ts, "session": session, "rule": rule, "path": path, "est_tokens": est}
            for ts, session, rule, path, est in guards]
    out.sort(key=lambda e: -e["ts"])
    return out[:limit]


def daily(days: int = 14, now: float | None = None) -> list[dict]:
    """Tokens saved per local day, oldest first, split into filters and the read guard."""
    if not _has_db():
        return []
    ratios = report_ratios(load())
    today = datetime.date.fromtimestamp(time.time() if now is None else now)
    first = today - datetime.timedelta(days=days - 1)
    buckets = {first + datetime.timedelta(days=i): [0.0, 0] for i in range(days)}
    since = time.mktime(first.timetuple())
    with store.db() as c:
        runs = c.execute("SELECT ts, raw_chars, out_chars, saved_default, saved_hex, saved_non_ascii FROM runs "
                         "WHERE ts >= ?", (since,)).fetchall()
        guards = c.execute("SELECT ts, est_tokens FROM guard WHERE ts >= ?", (since,)).fetchall()
    for ts, *chars in runs:
        b = buckets.get(datetime.date.fromtimestamp(ts))
        if b:
            b[0] += _saved_tokens(tuple(chars), ratios)
    for ts, est in guards:
        b = buckets.get(datetime.date.fromtimestamp(ts))
        if b:
            b[1] += est or 0
    return [{"day": d.isoformat(), "filter_tokens": round(f), "guard_tokens": g} for d, (f, g) in buckets.items()]


def _read(path: Path, cap: int) -> tuple[str, bool]:
    with open(path, "rb") as f:
        data = f.read(cap * 4 + 4)  # a char is at most 4 UTF-8 bytes
        more = bool(f.read(1))
    text = data.decode("utf-8", errors="replace")
    return text[:cap], more or len(text) > cap


def output(tee_id: str) -> dict | None:
    """The saved raw output and the output the agent got; None for an id that is not a saved run."""
    if not TEE_ID_RE.fullmatch(tee_id) or not _tee(f"{tee_id}.log").is_file():
        return None
    cap = load()["ui_max_output_chars"]
    original, original_cut = _read(_tee(f"{tee_id}.log"), cap)
    filtered, filtered_cut = None, False
    if _tee(f"{tee_id}.out.log").is_file():  # runs from before P4-T5 only kept the raw output
        filtered, filtered_cut = _read(_tee(f"{tee_id}.out.log"), cap)
    return {"tee_id": tee_id, "original": original, "original_truncated": original_cut,
            "filtered": filtered, "filtered_truncated": filtered_cut}


def _int(q: dict, name: str, default: int | None, hi: int | None) -> int | None:
    if name not in q:
        return default
    n = int(q[name][0])
    if n < 1:
        raise ValueError(name)
    return min(n, hi) if hi else n


class Handler(BaseHTTPRequestHandler):
    server_version = "toknix"

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data):
        self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):  # DNS rebinding
            return self._send(403, b"forbidden\n", "text/plain; charset=utf-8")
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        try:
            if url.path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/events":
                before = float(q["before"][0]) if "before" in q else None
                return self._json(events(_int(q, "limit", 200, 1000), before))
            if url.path == "/api/daily":
                return self._json(daily(_int(q, "days", 14, 90)))
            if url.path == "/api/summary":
                return self._json(gain_data(_int(q, "days", None, None), load()))
            if url.path.startswith("/api/output/"):
                o = output(url.path[len("/api/output/"):])
                if o is not None:
                    return self._json(o)
            return self._send(404, b"not found\n", "text/plain; charset=utf-8")
        except ValueError:
            return self._send(400, b"bad query\n", "text/plain; charset=utf-8")
        except Exception as e:  # a broken row must not take the server down
            return self._send(500, f"{type(e).__name__}: {e}\n".encode("utf-8"), "text/plain; charset=utf-8")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # on Windows SO_REUSEADDR would let us take a port in use


def make_server(port: int | None = None) -> Server:
    """`None`: the default port, or any free one when it is taken."""
    if port is None:
        try:
            return Server(("127.0.0.1", DEFAULT_PORT), Handler)
        except OSError:
            port = 0
    return Server(("127.0.0.1", port), Handler)


def serve(port: int | None = None, open_browser: bool = True) -> int:
    try:
        server = make_server(port)
    except OSError as e:
        print(f"toknix ui: cannot listen on 127.0.0.1:{port}: {e}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"toknix ui: {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
