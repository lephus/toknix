"""Reports (A6). v0.1 so far: `gain`, read from tokopt's own SQLite store."""
from __future__ import annotations

import json
import sqlite3
import sys
import time

from tokopt import store
from tokopt.config import load, tokopt_home
from tokopt.runner import write_out
from tokopt.textutil import DEFAULT_RATIOS, fmt_num


def _saved_tokens(row: tuple, ratios: dict) -> float:
    """Chars saved per class / ratio per class; rows from before P1-T5 only know total chars."""
    raw_chars, out_chars, d, h, n = row
    if d is None:
        return (raw_chars - out_chars) / ratios["default"]
    return d / ratios["default"] + h / ratios["hex"] + n / ratios["non_ascii"]


def gain_data(days: int | None = None, cfg: dict | None = None) -> dict:
    """Direct savings (filters + read guard), the re-read estimate (N1.1) and recall rates (N2.1)."""
    cfg = cfg or load()
    ratios = {**DEFAULT_RATIOS, **(cfg.get("token_ratios") or {})}
    since = time.time() - days * 86400 if days else 0.0
    runs, guard, recalled = [], [], set()
    if (tokopt_home() / "tokopt.db").exists():  # never create the db just to report on it
        with store.db() as c:
            runs = c.execute("SELECT kind, cmd, tee_id, error_overflow, raw_chars, out_chars, saved_default, "
                             "saved_hex, saved_non_ascii FROM runs WHERE ts >= ?", (since,)).fetchall()
            guard = c.execute("SELECT rule, COUNT(*), COALESCE(SUM(est_tokens), 0) FROM guard WHERE ts >= ? "
                              "GROUP BY rule", (since,)).fetchall()
            recalled = {r[0] for r in c.execute("SELECT DISTINCT tee_id FROM recalls")}

    kinds: dict = {}
    commands: dict = {}
    for kind, cmd, tee_id, overflow, *chars in runs:
        t = _saved_tokens(tuple(chars), ratios)
        k = kinds.setdefault(kind, {"kind": kind, "runs": 0, "cuts": 0, "recalled": 0, "error_overflow": 0,
                                    "saved": 0.0})
        k["runs"] += 1
        k["saved"] += t
        k["error_overflow"] += 1 if overflow else 0
        if tee_id:
            k["cuts"] += 1
            k["recalled"] += tee_id in recalled
        c = commands.setdefault(cmd, {"cmd": cmd, "runs": 0, "saved": 0.0})
        c["runs"] += 1
        c["saved"] += t

    for k in kinds.values():
        k["saved_tokens"] = round(k.pop("saved"))
        k["recall_rate"] = round(k["recalled"] / k["cuts"], 4) if k["cuts"] else None
        k["warn"] = bool(k["cuts"] and k["cuts"] >= cfg["recall_warn_min_cuts"]
                         and k["recall_rate"] > cfg["recall_warn_rate"])
    for c in commands.values():
        c["saved_tokens"] = round(c.pop("saved"))

    filter_tokens = sum(k["saved_tokens"] for k in kinds.values())
    guard_tokens = sum(g[2] for g in guard)
    direct = filter_tokens + guard_tokens
    w_cw, w_cr = cfg["weights"][1], cfg["weights"][2]
    r_bar = cfg["reread_default"]  # P4-T2: median from transcripts
    multiplier = w_cw + w_cr * r_bar
    return {
        "days": days,
        "no_data": not runs and not guard,
        "direct_tokens": direct,
        "filter_tokens": filter_tokens,
        "guard_tokens": guard_tokens,
        "reread": {"etu": round(direct * multiplier, 1), "multiplier": multiplier, "r_bar": r_bar,
                   "r_bar_source": "default"},
        "kinds": sorted(kinds.values(), key=lambda k: (-k["saved_tokens"], -k["runs"], k["kind"])),
        "error_overflow": sum(k["error_overflow"] for k in kinds.values()),
        "rules": [{"rule": r, "denials": n, "saved_tokens": t}
                  for r, n, t in sorted(guard, key=lambda g: (-g[2], g[0]))],
        "top_commands": sorted((c for c in commands.values() if c["saved_tokens"] > 0),
                               key=lambda c: (-c["saved_tokens"], c["cmd"]))[:10],
    }


def format_gain(d: dict, cfg: dict) -> str:
    if d["no_data"]:
        return "no data yet"
    r = d["reread"]
    w_cw, w_cr = cfg["weights"][1], cfg["weights"][2]
    out = [f"tokopt gain ({'last %d days' % d['days'] if d['days'] else 'all time'})", "",
           f"Saved directly: {fmt_num(d['direct_tokens'])} tokens (filters {fmt_num(d['filter_tokens'])}, "
           f"read guard {fmt_num(d['guard_tokens'])})",
           f"With re-reads (estimate): {fmt_num(r['etu'])} ETU = direct × {r['multiplier']:g} "
           f"(w_cw {w_cw:g} + w_cr {w_cr:g} × R̄ {r['r_bar']:g})"]
    if r["r_bar_source"] == "default":
        out.append(f"  R̄ = {r['r_bar']:g} is the default value, not measured from transcripts yet")

    if d["kinds"]:
        out += ["", "By filter kind", f"  {'kind':<12}{'runs':>6}{'cuts':>6}{'saved':>8}{'recalled':>10}{'rate':>6}"]
        for k in d["kinds"]:
            rate = f"{k['recall_rate']:.0%}" if k["recall_rate"] is not None else "—"
            out.append(f"  {k['kind']:<12}{k['runs']:>6}{k['cuts']:>6}{fmt_num(k['saved_tokens']):>8}"
                       f"{k['recalled']:>10}{rate:>6}")
        for k in d["kinds"]:
            if k["warn"]:
                out.append(f"  ⚠ filter '{k['kind']}' may cut too much: {k['recalled']} of {k['cuts']} cut outputs "
                           f"were recalled ({k['recall_rate']:.0%}); tell the tokopt authors which command")
    if d["error_overflow"]:
        out.append(f"  {d['error_overflow']} runs had more error lines than the token budget (all kept)")

    if d["rules"]:
        out += ["", "By read-guard rule", f"  {'rule':<12}{'denials':>8}{'saved':>8}"]
        out += [f"  {g['rule']:<12}{g['denials']:>8}{fmt_num(g['saved_tokens']):>8}" for g in d["rules"]]

    if d["top_commands"]:
        out += ["", "Top commands", f"  {'saved':>8}{'runs':>6}  command"]
        out += [f"  {fmt_num(c['saved_tokens']):>8}{c['runs']:>6}  {c['cmd'][:80]}" for c in d["top_commands"]]
    return "\n".join(out)


def gain(days: int | None = None, as_json: bool = False) -> int:
    cfg = load()
    try:
        d = gain_data(days, cfg)
    except sqlite3.DatabaseError as e:
        print(f"tokopt gain: cannot read {tokopt_home() / 'tokopt.db'}: {e}", file=sys.stderr)
        return 1
    write_out((json.dumps(d, indent=2) if as_json else format_gain(d, cfg)) + "\n")
    return 0
