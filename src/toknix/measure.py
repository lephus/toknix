"""Reports (A6): `gain` from toknix's own SQLite store, the char/token calibration (A6.4), and the
transcript reports built on src/toknix/transcripts.py."""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass

from toknix import store
from toknix.config import load, toknix_home
from toknix.runner import write_out
from toknix.textutil import DEFAULT_RATIOS, char_classes, fmt_num

CLASSES = ("default", "hex", "non_ascii")


def _saved_tokens(row: tuple, ratios: dict) -> float:
    """Chars saved per class / ratio per class; rows from before P1-T5 only know total chars."""
    raw_chars, out_chars, d, h, n = row
    if d is None:
        return (raw_chars - out_chars) / ratios["default"]
    return d / ratios["default"] + h / ratios["hex"] + n / ratios["non_ascii"]


def gain_data(days: int | None = None, cfg: dict | None = None) -> dict:
    """Direct savings (filters + read guard), the re-read estimate (N1.1) and recall rates (N2.1)."""
    cfg = cfg or load()
    ratios = report_ratios(cfg)
    since = time.time() - days * 86400 if days else 0.0
    runs, guard, recalled = [], [], set()
    if (toknix_home() / "toknix.db").exists():  # never create the db just to report on it
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
    from toknix import pricing, reports  # transcripts are only read here, never by the hooks
    pr = pricing.Pricer(cfg)
    bash = reports.bash_stats(cfg["gain_transcript_days"], cfg)
    if bash["r_values"]:  # P8-T2: each Bash result at the price of the request that carried it
        r_bar, source, multiplier = statistics.median(bash["r_values"]), "transcripts", \
            statistics.median(bash["multipliers"])
    else:
        w = pr.weights(pr.etu_model)  # etu_model, 5-minute writes; `weights` when it has no price (A1)
        r_bar, source, multiplier = cfg["reread_default"], "default", w[1] + w[3] * cfg["reread_default"]
    etu = direct * multiplier
    return {
        "days": days,
        "no_data": not runs and not guard,
        "direct_tokens": direct,
        "filter_tokens": filter_tokens,
        "guard_tokens": guard_tokens,
        "reread": {"etu": round(etu, 1), "usd": etu * pr.ref / 1e6 if pr.ref else None, "multiplier": multiplier,
                   "r_bar": r_bar, "r_bar_source": source, "etu_model": pr.etu_model if pr.ref else None},
        "r_bar_samples": len(bash["r_values"]),
        "wrapped": {"bash_calls": bash["bash_calls"], "wrapped": bash["wrapped"],
                    "share": bash["wrapped"] / bash["bash_calls"] if bash["bash_calls"] else None},
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
    from toknix.pricing import Pricer, fmt_usd
    r = d["reread"]
    usd = f" ≈ {fmt_usd(r['usd'])}" if r["usd"] is not None else ""
    if r["r_bar_source"] == "default":
        w = Pricer(cfg).weights(r["etu_model"] or "")
        how = (f"w_cw {w[1]:g} + w_cr {w[3]:g} × R̄ {r['r_bar']:g}, "
               + (f"{r['etu_model']} prices" if r["etu_model"] else "`weights`"))
    else:
        how = f"median of w_cw + w_cr × R over {d['r_bar_samples']} Bash results, each at its model's price"
    out = [f"toknix gain ({'last %d days' % d['days'] if d['days'] else 'all time'})", "",
           f"Saved directly: {fmt_num(d['direct_tokens'])} tokens (filters {fmt_num(d['filter_tokens'])}, "
           f"read guard {fmt_num(d['guard_tokens'])})",
           f"With re-reads (estimate): {fmt_num(r['etu'])} ETU{usd} = direct × {r['multiplier']:.3g} ({how})"]
    if r["r_bar_source"] == "default":
        out.append(f"  R̄ = {r['r_bar']:g} is the default value, not measured from transcripts yet")
    else:
        out.append(f"  R̄ = {r['r_bar']:g}: median requests left after {d['r_bar_samples']} Bash results "
                   f"(transcripts of the last {cfg['gain_transcript_days']} days)")
    wr = d["wrapped"]
    if wr["bash_calls"]:
        out.append(f"Bash calls wrapped by toknix: {wr['wrapped']} of {wr['bash_calls']} ({wr['share']:.0%}, "
                   f"transcripts of the last {cfg['gain_transcript_days']} days)")

    if d["kinds"]:
        out += ["", "By filter kind", f"  {'kind':<12}{'runs':>6}{'cuts':>6}{'saved':>8}{'recalled':>10}{'rate':>6}"]
        for k in d["kinds"]:
            rate = f"{k['recall_rate']:.0%}" if k["recall_rate"] is not None else "—"
            out.append(f"  {k['kind']:<12}{k['runs']:>6}{k['cuts']:>6}{fmt_num(k['saved_tokens']):>8}"
                       f"{k['recalled']:>10}{rate:>6}")
        for k in d["kinds"]:
            if k["warn"]:
                out.append(f"  ⚠ filter '{k['kind']}' may cut too much: {k['recalled']} of {k['cuts']} cut outputs "
                           f"were recalled ({k['recall_rate']:.0%}); tell the toknix authors which command")
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
        print(f"toknix gain: cannot read {toknix_home() / 'toknix.db'}: {e}", file=sys.stderr)
        return 1
    write_out((json.dumps(d, indent=2) if as_json else format_gain(d, cfg)) + "\n")
    return 0


# --------------------------------------------------------------------------- calibration (A6.4)
@dataclass
class Calibration:
    ratios: dict
    samples: int
    median_error: float


def calibration_path():
    return toknix_home() / "calibration.json"


def report_ratios(cfg: dict) -> dict:
    """Ratios for reports: config, overridden by a calibration that passed its quality bar. The filters
    never call this; they always use the config (A1), so what the agent sees never shifts on its own."""
    ratios = {**DEFAULT_RATIOS, **(cfg.get("token_ratios") or {})}
    try:
        cal = json.loads(calibration_path().read_text(encoding="utf-8")).get("claude") or {}
        if cal.get("median_error", 1) < cfg["calibration_max_error"]:
            ratios.update({k: float(v) for k, v in (cal.get("ratios") or {}).items() if k in CLASSES and v > 0})
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return ratios


def cache_break(prev, cur, cfg: dict) -> bool:
    """A6.5: the cache read collapses while a big cache write happens."""
    return cur.cache_read < 0.5 * (prev.cache_read + prev.cache_write) and cur.cache_write >= cfg["cache_break_min_write"]


def calibration_samples(sessions, cfg: dict) -> tuple[list, list]:
    """x = chars per class of the tool results between two requests, y = context growth they caused.
    Only pairs with nothing else in between: no compaction, no cache break, no user text."""
    xs, ys = [], []
    for s in sessions:
        if s.agent != "claude":
            continue
        main = s.main
        for prev, cur in zip(main, main[1:]):
            if cur.compacted_before or cur.user_text_before or not cur.results_before or cache_break(prev, cur, cfg):
                continue
            y = cur.context - prev.context - prev.output
            if y <= 0:
                continue
            cc = [0, 0, 0]
            for res in cur.results_before:
                c = char_classes(res.text)
                cc = [cc[j] + c[k] for j, k in enumerate(CLASSES)]
            xs.append(cc)
            ys.append(y)
    return xs, ys


def _solve(xs, ys, cols) -> list[float] | None:
    """Least squares without intercept on the given columns: the normal equations by Gauss elimination."""
    n = len(cols)
    a = [[float(sum(x[i] * x[j] for x in xs)) for j in cols] + [float(sum(x[i] * y for x, y in zip(xs, ys)))]
         for i in cols]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-12:
            return None
        a[c], a[piv] = a[piv], a[c]
        for r in range(n):
            if r != c:
                f = a[r][c] / a[c][c]
                a[r] = [v - f * w for v, w in zip(a[r], a[c])]
    return [a[i][n] / a[i][i] for i in range(n)]


def calibrate(sessions, cfg: dict | None = None) -> Calibration | None:
    """Fit tokens = Σ chars_class · coef_class on 4/5 of the samples, drop outliers, refit, and measure the
    median relative error on the other 1/5. None below calibration_min_samples or when the fit fails."""
    cfg = cfg or load()
    xs, ys = calibration_samples(sessions, cfg)
    if len(xs) < cfg["calibration_min_samples"]:
        return None
    test = [k for k in range(len(xs)) if k % 5 == 4]
    train = [k for k in range(len(xs)) if k % 5 != 4]
    cols = [j for j in range(3) if any(xs[k][j] for k in train)]
    coef = _solve([xs[k] for k in train], [ys[k] for k in train], cols)
    if coef is None:
        return None

    def predict(x, c):
        return sum(x[j] * c[i] for i, j in enumerate(cols))

    resid = [abs(predict(xs[k], coef) - ys[k]) for k in train]
    cut = 3 * statistics.median(resid) or float("inf")
    kept = [k for k, r in zip(train, resid) if r <= cut]
    coef = _solve([xs[k] for k in kept], [ys[k] for k in kept], cols) or coef
    errors = [abs(predict(xs[k], coef) - ys[k]) / ys[k] for k in test]
    if any(c <= 0 for c in coef):
        return Calibration({}, len(xs), float("inf"))
    base = {**DEFAULT_RATIOS, **(cfg.get("token_ratios") or {})}
    ratios = {k: (1 / coef[cols.index(j)] if j in cols else base[k]) for j, k in enumerate(CLASSES)}
    return Calibration(ratios, len(xs), statistics.median(errors) if errors else float("inf"))


def update_calibration(sessions, cfg: dict | None = None) -> bool:
    """Write calibration.json only when the fit is good enough; otherwise leave things as they are."""
    cfg = cfg or load()
    cal = calibrate(sessions, cfg)
    if cal is None or cal.median_error >= cfg["calibration_max_error"]:
        return False
    path = calibration_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"claude": {"ratios": {k: round(v, 4) for k, v in cal.ratios.items()},
                                           "samples": cal.samples, "median_error": round(cal.median_error, 4),
                                           "ts": time.time()}}, indent=2) + "\n", encoding="utf-8")
    return True
