"""Local SQLite store: filtered runs, guard events, per-session read ledger. Nothing leaves the machine."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from tokopt.config import tokopt_home

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  ts REAL, kind TEXT, cmd TEXT, exit INTEGER,
  raw_chars INTEGER, out_chars INTEGER, raw_lines INTEGER, out_lines INTEGER, tee_id TEXT);
CREATE TABLE IF NOT EXISTS guard(
  ts REAL, session TEXT, rule TEXT, path TEXT, est_tokens INTEGER);
CREATE TABLE IF NOT EXISTS reads(
  session TEXT, path TEXT, mtime REAL, span TEXT, n INTEGER,
  PRIMARY KEY(session, path, span));
"""


@contextmanager
def db():
    home = tokopt_home()
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(home / "tokopt.db"), timeout=5)
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def record_run(kind, cmd, exit_code, raw, out, raw_lines, out_lines, tee_id):
    try:
        with db() as c:
            c.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)",
                      (time.time(), kind, cmd[:300], exit_code, raw, out, raw_lines, out_lines, tee_id))
    except sqlite3.Error:
        pass  # stats are best-effort; never break the agent's command


def record_guard(session, rule, path, est):
    try:
        with db() as c:
            c.execute("INSERT INTO guard VALUES(?,?,?,?,?)", (time.time(), session, rule, path, est))
    except sqlite3.Error:
        pass


def bump_read(session: str, path: str, mtime: float, span: str) -> int:
    """Count identical reads of an unchanged file in this session. Returns the new count."""
    with db() as c:
        row = c.execute("SELECT mtime, n FROM reads WHERE session=? AND path=? AND span=?",
                        (session, path, span)).fetchone()
        if row and abs(row[0] - mtime) < 1e-6:
            n = row[1] + 1
        else:
            n = 1
        c.execute("INSERT OR REPLACE INTO reads VALUES(?,?,?,?,?)", (session, path, mtime, span, n))
        return n


def reset_reads(session: str):
    with db() as c:
        c.execute("DELETE FROM reads WHERE session=?", (session,))
