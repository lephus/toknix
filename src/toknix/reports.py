"""Transcript reports (A6.2): `report`, `waste`, `context`, `snapshot`, plus the transcript numbers `gain`
uses (R̄ and the share of Bash calls toknix wrapped). Costs count every chain of a session, subagents
included (P8-T1); request counts are those of the main chain, and re-reads are counted within each chain.
Each request is priced at its own model (A1, P8-T2): ETU adds up across models, and $ comes with it.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from toknix import filters, measure, pricing, transcripts
from toknix.config import claude_dir, codex_dir, gemini_dir, is_junk, load, toknix_home
from toknix.pricing import fmt_usd
from toknix.runner import write_out
from toknix.textutil import est_tokens, fmt_num

GROUPS = ["tool_families", "big_outputs", "junk", "repeated_reads", "long_sessions", "cache_breaks", "output_heavy",
          "subagents"]
HINTS = {
    "tool_families": "biggest re-read cost first: filter these commands with `toknix run`, or read a range "
                     "(`toknix outline` / `toknix symbol`) instead of whole files",
    "big_outputs": "each stays in context for every later request: cut it at the source (`toknix run`, "
                   "--quiet flags, offset/limit) or start a new session after it",
    "junk": "generated, vendored or lock files: read the source or manifest; `toknix install` adds a read guard",
    "repeated_reads": "the content is still in context: use it, or read only the changed range",
    "long_sessions": "every request re-reads the whole context: one task per session, /clear between tasks",
    "cache_breaks": "idle → /clear or accept it after a long break; model → don't switch model mid-session; "
                    "unknown → check MCP servers or instruction files changed during the session",
    "output_heavy": "see the breakdown: Write → edit existing files with Edit; text → shorter answers "
                    "(rules block); thinking → lower effort for simple tasks (toknix never changes it)",
    "subagents": "agents that edited no file only read, searched or ran commands: give them a cheaper model "
                 "(`model: sonnet` in the agent definition or the Agent call); forks keep the parent's model "
                 "to reuse its cache",
}


class Cost:
    """ETU, $ (of the priced requests; None when none was priced) and output ETU, added up."""
    __slots__ = ("etu", "usd", "out")

    def __init__(self):
        self.etu, self.usd, self.out = 0.0, None, 0.0

    def add(self, etu: float, usd: float | None, out: float = 0.0) -> "Cost":
        self.etu += etu
        self.out += out
        self.usd = _add(self.usd, usd)
        return self


def _add(a: float | None, b: float | None) -> float | None:
    return a if b is None else b if a is None else a + b


def remaining(chain: list) -> list[int]:
    """For each request index: how many later requests re-read what it carried, up to the next compaction."""
    out, left = [0] * len(chain), 0
    for j in range(len(chain) - 1, -1, -1):
        out[j] = left
        left = 0 if chain[j].compacted_before else left + 1
    return out


def results(s) -> list[tuple]:
    """(tool_result, its tool_use or None, the request that carried it, requests of its chain left after), for
    every chain: a subagent re-reads its own context, not the session's."""
    uses = {u.id: u for r in s.requests for u in r.tool_uses}
    out = []
    for chain in s.chains:
        left = remaining(chain)
        out += [(res, uses.get(res.tool_use_id), r, left[j]) for j, r in enumerate(chain) for res in r.results_before]
    return out


def family(use) -> str:
    if use is None:
        return "?"
    inp = use.input
    if use.name in ("Bash", "shell"):
        cmd = inp.get("command")
        cmd = " ".join(cmd) if isinstance(cmd, list) else cmd if isinstance(cmd, str) else ""
        prog = filters.program_of(cmd)[0] or "?"
        if prog == "bash" and isinstance(inp.get("command"), list):  # codex: ["bash", "-lc", "npm test"]
            prog = filters.program_of(inp["command"][-1])[0] or "?"
        return f"Bash:{prog}"
    if use.name == "Read":
        return f"Read:{inp.get('file_path', '?')}"
    return use.name or "?"


def _breakdown(r, ratio: float) -> dict:
    """A6.6: output_tokens split by the chars of each kind of block; a hidden thinking gets the rest."""
    visible = {"text": r.text_chars, "write_tools": r.write_input_chars, "other_tools": r.other_input_chars}
    if r.thinking_hidden and not r.thinking_chars:
        est = {k: int(v / ratio) for k, v in visible.items()}
        shown = sum(est.values())
        if shown > r.output:  # the estimate overshoots: scale it to what was billed
            est = {k: v * r.output / shown for k, v in est.items()}
            shown = r.output
        return {"thinking": r.output - shown, **est}
    chars = {"thinking": r.thinking_chars, **visible}
    total = sum(chars.values())
    return {k: (r.output * v / total if total else 0) for k, v in chars.items()}


def _sum_breakdowns(reqs, ratio: float) -> dict:
    out = Counter()
    for r in reqs:
        out.update(_breakdown(r, ratio))
    return {k: int(round(out[k])) for k in ("thinking", "text", "write_tools", "other_tools")}


def _project(s) -> str:
    return s.path.parent.name if s.agent == "claude" else "codex"


# --------------------------------------------------------------------------- report
UNKNOWN_MODEL = "(unknown)"


def report_data(days: int, cfg: dict | None = None, calibrate: bool = True) -> dict:
    cfg = cfg or load()
    pr, ratios = pricing.Pricer(cfg), measure.report_ratios(cfg)
    ss = transcripts.sessions(days)
    cal = None
    if calibrate and ss:
        updated = measure.update_calibration(ss, cfg)
        c = measure.calibrate(ss, cfg)
        cal = {"updated": updated, "samples": c.samples if c else len(measure.calibration_samples(ss, cfg)[0]),
               "median_error": c.median_error if c else None, "ratios": c.ratios if c and updated else None}
        ratios = measure.report_ratios(cfg)
    totals, total, sub = Counter(), Cost(), Cost()
    models: dict[str, dict] = {}
    rows = []
    tool_out = sub_chains = sub_requests = all_requests = 0
    for s in ss:
        totals.update(s.totals())
        cost, sub_cost = Cost(), Cost()
        for r in s.requests:
            c = pr.cost(r)
            cost.add(*c)
            if r.sidechain:
                sub_cost.add(*c)
            m = models.setdefault(r.model or UNKNOWN_MODEL, {"model": r.model or UNKNOWN_MODEL, "requests": 0,
                                                              "etu": 0.0, "usd": None})
            m["requests"] += 1
            m["etu"] += c[0]
            m["usd"] = _add(m["usd"], c[1])
        total.add(cost.etu, cost.usd, cost.out)
        sub.add(sub_cost.etu, sub_cost.usd)
        sub_chains += len(s.chains) - 1
        sub_requests += len(s.requests) - len(s.main)
        all_requests += len(s.requests)
        rows.append({"id": s.id, "agent": s.agent, "project": _project(s), "requests": len(s.main),
                     "etu": cost.etu, "usd": cost.usd, "output_share": cost.out / cost.etu if cost.etu else 0.0,
                     "subagent_share": sub_cost.etu / cost.etu if cost.etu else 0.0})
        tool_out += sum(est_tokens(res.text, ratios) for res, *_ in results(s))
    tokens = {k: totals[k] for k in ("input", "cache_write", "cache_read", "output")}
    n_req = sum(r["requests"] for r in rows)
    context_tokens = tokens["input"] + tokens["cache_write"] + tokens["cache_read"]
    for m in models.values():
        m["share"] = m["etu"] / total.etu if total.etu else 0.0
    unpriced = [m for m in models.values() if m["usd"] is None]
    return {
        "days": days, "sessions": len(ss), "requests": n_req, "tokens": tokens, "etu": total.etu,
        "usd": total.usd, "etu_model": pr.etu_model if pr.ref else None, "etu_model_usd": pr.ref,
        "etu_per_session": total.etu / len(ss) if ss else 0.0,
        "etu_per_request": total.etu / all_requests if all_requests else 0.0,
        "cache_hit": tokens["cache_read"] / context_tokens if context_tokens else 0.0,
        "output_share": total.out / total.etu if total.etu else 0.0,
        "subagents": {"chains": sub_chains, "requests": sub_requests, "etu": sub.etu, "usd": sub.usd,
                      "share": sub.etu / total.etu if total.etu else 0.0},
        "models": sorted(models.values(), key=lambda m: (-m["etu"], m["model"])),
        "unpriced": {"requests": sum(m["requests"] for m in unpriced), "models": sorted(m["model"] for m in unpriced)},
        "tool_output_tokens": tool_out,
        "output_breakdown": _sum_breakdowns([r for s in ss for r in s.requests], ratios["default"]),
        "top_sessions": sorted(rows, key=lambda r: -r["etu"])[:10],
        "calibration": cal,
    }


def format_report(d: dict) -> str:
    if not d["sessions"]:
        return f"no transcripts in the last {d['days']} days"
    t, sub = d["tokens"], d["subagents"]
    usd = f" ≈ {fmt_usd(d['usd'])} at API list prices" if d["usd"] is not None else ""
    unit = (f"1 ETU = one {d['etu_model']} input token ({fmt_usd(d['etu_model_usd'])}/MTok)" if d["etu_model"]
            else "ETU from `weights` (etu_model has no price)")
    out = [f"toknix report (last {d['days']} days)", "",
           f"{d['sessions']} sessions, {d['requests']} requests; {fmt_num(d['etu'])} ETU{usd} "
           f"({fmt_num(d['etu_per_session'])}/session, {fmt_num(d['etu_per_request'])}/request)",
           unit,
           f"subagents: {sub['chains']} agents, {sub['requests']} requests, {fmt_num(sub['etu'])} ETU"
           + (f" ≈ {fmt_usd(sub['usd'])}" if sub["usd"] is not None else "") + f" ({sub['share']:.0%} of ETU)",
           f"tokens: input {fmt_num(t['input'])}, cache write {fmt_num(t['cache_write'])}, "
           f"cache read {fmt_num(t['cache_read'])}, output {fmt_num(t['output'])}",
           f"cache hit {d['cache_hit']:.0%}; output share {d['output_share']:.0%} of ETU; "
           f"tool output ~{fmt_num(d['tool_output_tokens'])} tokens (estimated)"]
    b = d["output_breakdown"]
    if sum(b.values()):
        out.append("output: " + ", ".join(f"{k.replace('_', ' ')} {fmt_num(v)}" for k, v in b.items()))
    out += ["", "By model", f"  {'ETU':>8}  {'$':>8}  {'share':>5}  {'requests':>8}  model"]
    out += [f"  {fmt_num(m['etu']):>8}  {fmt_usd(m['usd']):>8}  {m['share']:>5.0%}  {m['requests']:>8}  {m['model']}"
            for m in d["models"]]
    if d["unpriced"]["requests"]:
        out.append(f"  no price for {d['unpriced']['requests']} requests ({', '.join(d['unpriced']['models'])}): "
                   "their ETU uses `weights`, they have no $")
    out += ["", "Top sessions", f"  {'ETU':>8}  {'$':>8}  {'requests':>8}  {'output':>6}  {'sub':>4}  session"]
    out += [f"  {fmt_num(r['etu']):>8}  {fmt_usd(r['usd']):>8}  {r['requests']:>8}  {r['output_share']:>6.0%}  "
            f"{r['subagent_share']:>4.0%}  {r['project']}/{r['id']}" for r in d["top_sessions"]]
    c = d["calibration"]
    if c is None:
        cal = "not run"
    elif c["updated"]:
        cal = "updated from {} request pairs: {}".format(
            c["samples"], ", ".join(f"{k} {v:.2f} chars/token" for k, v in c["ratios"].items()))
    elif c["median_error"] is None:
        cal = f"{c['samples']} request pairs, not enough (need {load()['calibration_min_samples']}); config ratios used"
    else:
        cal = f"median error {c['median_error']:.0%} on {c['samples']} pairs, too high; config ratios used"
    out += ["", f"calibration: {cal}"]
    return "\n".join(out)


# --------------------------------------------------------------------------- waste
def _cache_breaks(s, cfg: dict, pr: pricing.Pricer) -> list[dict]:
    """A6.5, with the cause by priority: compact, model, idle (gap over the cache TTL), unknown; each chain
    has its own cache."""
    out = []
    for chain in s.chains:
        out += _chain_breaks(s, chain, cfg, pr)
    return out


def _chain_breaks(s, chain: list, cfg: dict, pr: pricing.Pricer) -> list[dict]:
    out = []
    one_hour = False
    for prev, cur in zip(chain, chain[1:]):
        one_hour = one_hour or prev.cache_write_1h > 0
        if not measure.cache_break(prev, cur, cfg):
            continue
        ttl = 3600 if one_hour else cfg["cache_ttl_seconds"]
        if cur.compacted_before:
            cause = "compact"
        elif cur.model and prev.model and cur.model != prev.model:
            cause = "model"
        elif cur.ts and prev.ts and cur.ts - prev.ts > ttl:
            cause = "idle"
        else:
            cause = "unknown"
        lost, usd = (0.0, 0.0) if cause == "compact" else pr.write_loss(cur)
        out.append({"session": s.id, "cause": cause, "cache_write": cur.cache_write, "etu_lost": lost,
                    "usd_lost": usd, "gap_seconds": (cur.ts - prev.ts) if cur.ts and prev.ts else None})
    return out


def _subagent(s, chain: list, pr: pricing.Pricer, cheap: str) -> dict:
    """One subagent file (P8-T3): its type from `agent-<id>.meta.json`, the model it spent most ETU on, its cost,
    whether it edited a file, and what its tokens would have cost on `cheap`."""
    meta = _json(s.path.parent / s.path.stem / "subagents" / f"{chain[0].chain}.meta.json")
    kind = meta.get("agentType")
    by_model, cost = Counter(), Cost()
    for r in chain:
        c = pr.cost(r)
        cost.add(*c)
        by_model[r.model or UNKNOWN_MODEL] += c[0]
    return {"type": kind if isinstance(kind, str) and kind else "?", "model": by_model.most_common(1)[0][0],
            "requests": len(chain), "etu": cost.etu, "usd": cost.usd,
            "cheap_usd": sum(pr.usd_as(r, cheap) or 0.0 for r in chain),
            "edited": any(u.name in transcripts.WRITE_TOOLS for r in chain for u in r.tool_uses)}


def _subagent_groups(agents: list[dict], pr: pricing.Pricer, cheap: str, all_etu: float) -> dict:
    """waste (8): subagents by (type, model); a cheaper model is suggested for the agents that edited no file,
    unless they are forks (a fork reads its parent's cache, and a cache belongs to one model)."""
    cheap_price = (pr.rates(cheap) or [None])[0]
    rows: dict[tuple, dict] = {}
    for a in agents:
        g = rows.setdefault((a["type"], a["model"]), {
            "agent_type": a["type"], "model": a["model"], "agents": 0, "requests": 0, "etu": 0.0, "usd": None,
            "no_edit_agents": 0, "no_edit_usd": None, "cheaper_model": None, "saving_usd": None})
        g["agents"] += 1
        g["requests"] += a["requests"]
        g["etu"] += a["etu"]
        g["usd"] = _add(g["usd"], a["usd"])
        if a["edited"]:
            continue
        g["no_edit_agents"] += 1
        g["no_edit_usd"] = _add(g["no_edit_usd"], a["usd"])
        price = (pr.rates(a["model"]) or [None])[0]
        if a["type"] != "fork" and price is not None and cheap_price is not None and price > cheap_price:
            g["cheaper_model"] = cheap
            g["saving_usd"] = _add(g["saving_usd"], a["usd"] - a["cheap_usd"])
    items = sorted(rows.values(), key=lambda g: (-g["etu"], g["agent_type"], g["model"]))
    saving = None
    for g in items:
        g["requests_per_agent"] = g["requests"] / g["agents"]
        g["share"] = g["etu"] / all_etu if all_etu else 0.0
        saving = _add(saving, g["saving_usd"])
    etu = sum(g["etu"] for g in items)
    return {"items": items, "agents": len(agents), "etu": etu, "share": etu / all_etu if all_etu else 0.0,
            "saving_usd": saving}


def waste_data(days: int, cfg: dict | None = None) -> dict:
    cfg = cfg or load()
    pr, ratios = pricing.Pricer(cfg), measure.report_ratios(cfg)
    ss = transcripts.sessions(days)
    fams: dict[str, dict] = {}
    big, junk, repeated, long_sessions, breaks, heavy, agents = [], [], [], [], [], [], []
    cheap, all_etu = cfg["waste_subagent_model"], 0.0
    for s in ss:
        reads = Counter()  # (chain, path): content read earlier in another chain is not in this one's context
        for res, use, req, left in results(s):
            t = est_tokens(res.text, ratios)
            cost, usd = pr.reread(req, t, left), pr.reread_usd(req, t, left)  # reread_cost (A1)
            f = family(use)
            agg = fams.setdefault(f, {"family": f, "calls": 0, "tokens": 0, "etu": 0.0, "usd": None})
            agg["calls"] += 1
            agg["tokens"] += t
            agg["etu"] += cost
            agg["usd"] = _add(agg["usd"], usd)
            if t >= cfg["waste_big_output_tokens"]:
                big.append({"session": s.id, "family": f, "tokens": t, "remaining_requests": left, "etu": cost,
                            "usd": usd})
        for r in s.requests:
            for use in r.tool_uses:
                target = use.input.get("file_path") or use.input.get("path") if use.name in (
                    "Read", "Grep", "Glob") else None
                if use.name == "Read" and isinstance(target, str):
                    reads[r.chain, target] += 1
                if isinstance(target, str) and is_junk(target.rstrip("/") + ("/" if use.name != "Read" else ""),
                                                       cfg["junk_patterns"]):
                    junk.append({"session": s.id, "tool": use.name, "path": target})
                elif use.name == "Bash" and isinstance(use.input.get("command"), str):
                    words = [x for x in re.split(r"[\s'\"=]+", use.input["command"]) if "/" in x or "." in x]
                    hit = next((x for x in words if is_junk(x, cfg["junk_patterns"])), None)
                    if hit:
                        junk.append({"session": s.id, "tool": "Bash", "path": hit})
        repeated += [{"session": s.id, "path": p, "reads": n} for (_, p), n in sorted(reads.items())
                     if n >= cfg["waste_reads_per_session"]]
        cost = Cost()
        for r in s.requests:
            cost.add(*pr.cost(r))
        all_etu += cost.etu
        agents += [_subagent(s, chain, pr, cheap) for chain in s.chains[1:] if chain[0].chain.startswith("agent-")]
        if len(s.main) > cfg["waste_max_requests"]:
            long_sessions.append({"session": s.id, "requests": len(s.main), "etu": cost.etu, "usd": cost.usd})
        breaks += _cache_breaks(s, cfg, pr)
        if cost.etu and cost.out / cost.etu > cfg["waste_output_share"]:
            heavy.append({"session": s.id, "output_share": cost.out / cost.etu,
                          "breakdown": _sum_breakdowns(s.requests, ratios["default"])})
    lost_usd = None
    for b in breaks:
        lost_usd = _add(lost_usd, b["usd_lost"])
    groups = {
        "tool_families": {"items": sorted(fams.values(), key=lambda x: -x["etu"])[:10]},
        "big_outputs": {"items": sorted(big, key=lambda x: -x["etu"])[:10]},
        "junk": {"items": junk},
        "repeated_reads": {"items": repeated},
        "long_sessions": {"items": sorted(long_sessions, key=lambda x: -x["requests"])},
        "cache_breaks": {"items": breaks, "etu_lost": sum(b["etu_lost"] for b in breaks), "usd_lost": lost_usd},
        "output_heavy": {"items": sorted(heavy, key=lambda x: -x["output_share"])},
        "subagents": _subagent_groups(agents, pr, cheap, all_etu),
    }
    for name, g in groups.items():
        g["hint"] = HINTS[name]
    return {"days": days, "sessions": len(ss), "groups": groups}


def format_waste(d: dict) -> str:
    if not d["sessions"]:
        return f"no transcripts in the last {d['days']} days"
    g = d["groups"]
    out = [f"toknix waste (last {d['days']} days, {d['sessions']} sessions)"]

    def section(n: int, title: str, name: str, lines: list[str]):
        out.extend(["", f"({n}) {title}"] + (lines or ["  none"]) + [f"  fix: {HINTS[name]}"])

    section(1, "tool families by re-read cost", "tool_families",
            [f"  {fmt_num(x['etu']):>8} ETU  {fmt_usd(x['usd']):>7}  {fmt_num(x['tokens']):>7} tokens  "
             f"{x['calls']:>4}×  {x['family'][:70]}" for x in g["tool_families"]["items"]])
    section(2, f"single outputs ≥ {fmt_num(load()['waste_big_output_tokens'])} tokens", "big_outputs",
            [f"  {fmt_num(x['etu']):>8} ETU  {fmt_usd(x['usd']):>7}  {fmt_num(x['tokens']):>7} tokens  "
             f"re-read {x['remaining_requests']}×  {x['family'][:60]}" for x in g["big_outputs"]["items"]])
    section(3, "reads and commands on junk paths", "junk",
            [f"  {x['tool']:<5} {x['path']}" for x in g["junk"]["items"][:15]])
    section(4, "files read 3+ times in a session", "repeated_reads",
            [f"  {x['reads']}×  {x['path']}" for x in g["repeated_reads"]["items"][:15]])
    section(5, f"sessions over {load()['waste_max_requests']} requests", "long_sessions",
            [f"  {x['requests']:>4} requests  {fmt_num(x['etu'])} ETU  {fmt_usd(x['usd'])}  {x['session']}"
             for x in g["long_sessions"]["items"][:10]])
    breaks = g["cache_breaks"]["items"]
    by_cause = Counter(b["cause"] for b in breaks)
    lost_usd = g["cache_breaks"]["usd_lost"]
    lost = fmt_num(g["cache_breaks"]["etu_lost"]) + " ETU" + (f", {fmt_usd(lost_usd)}" if lost_usd is not None else "")
    section(6, f"cache breaks ({lost} lost; compactions are normal)",
            "cache_breaks", [f"  {by_cause[c]:>4}× {c}" for c in ("compact", "model", "idle", "unknown") if by_cause[c]])
    section(7, f"sessions where output is over {load()['waste_output_share']:.0%} of ETU", "output_heavy",
            [f"  {x['output_share']:>4.0%}  {x['session']}  "
             + ", ".join(f"{k.replace('_', ' ')} {fmt_num(v)}" for k, v in x["breakdown"].items())
             for x in g["output_heavy"]["items"][:10]])
    sub = g["subagents"]
    lines = []
    for x in sub["items"][:10]:
        lines.append(f"  {fmt_num(x['etu']):>8} ETU  {fmt_usd(x['usd']):>8}  {x['agents']:>4} agents  "
                     f"{x['requests_per_agent']:>5.1f} req/agent  {x['no_edit_agents']:>4} edited no file "
                     f"({fmt_usd(x['no_edit_usd'])})  {x['agent_type']} / {x['model']}")
        if x["saving_usd"]:
            lines.append(f"  {'':>8}     → {x['cheaper_model']} would cost ~{fmt_usd(x['saving_usd'])} less for its "
                         f"{x['no_edit_agents']} agents that edited no file (estimate)")
    section(8, f"subagents by type and model ({sub['agents']} agents, {sub['share']:.0%} of ETU)", "subagents", lines)
    return "\n".join(out)


# --------------------------------------------------------------------------- context
def _skill_name(skill_md: Path) -> str:
    try:
        m = re.search(r"^name:\s*(.+)$", skill_md.read_text(encoding="utf-8", errors="replace"), re.M)
    except OSError:
        m = None
    return m.group(1).strip().strip("\"'") if m else skill_md.parent.name


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def context_data(days: int, directory: str | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or load()
    ratios = measure.report_ratios(cfg)
    cwd = Path(directory or ".").resolve()
    up = [cwd] + [p for p in cwd.parents if p != Path(p.anchor)]
    candidates = [claude_dir() / "CLAUDE.md"] + sorted((claude_dir() / "rules").glob("*.md"))
    for d in up:
        candidates += [d / "CLAUDE.md", d / "CLAUDE.local.md"] + sorted((d / ".claude" / "rules").glob("*.md"))
        candidates += [d / "AGENTS.md", d / "GEMINI.md"]
    candidates += [codex_dir() / "AGENTS.md", gemini_dir() / "GEMINI.md"]
    seen, files = set(), []
    for p in candidates:
        key = p.resolve() if p.exists() else None
        if key is None or key in seen or not p.is_file():
            continue
        seen.add(key)
        files.append({"path": str(p), "tokens": est_tokens(p.read_text(encoding="utf-8", errors="replace"), ratios)})

    servers: dict[str, str] = {}
    user_cfg = _json(Path.home() / ".claude.json")
    for name in (user_cfg.get("mcpServers") or {}):
        servers.setdefault(name, "~/.claude.json")
    for key, proj in (user_cfg.get("projects") or {}).items():
        try:
            if Path(key).resolve() == cwd and isinstance(proj, dict):
                for name in (proj.get("mcpServers") or {}):
                    servers.setdefault(name, "~/.claude.json (this project)")
        except (OSError, ValueError):
            continue
    for f in (cwd / ".mcp.json", cwd / ".claude" / "settings.json", cwd / ".claude" / "settings.local.json"):
        for name in (_json(f).get("mcpServers") or {}):
            servers.setdefault(name, str(f.relative_to(cwd)))

    skills: dict[str, str] = {}
    for where, root in (("user", claude_dir() / "skills"), ("project", cwd / ".claude" / "skills")):
        for md in sorted(root.glob("*/SKILL.md")):
            skills.setdefault(_skill_name(md), where)
    for md in sorted((claude_dir() / "plugins").glob("**/skills/*/SKILL.md")):
        skills.setdefault(_skill_name(md), "plugin")

    mcp_calls, skill_calls = Counter(), Counter()
    for s in transcripts.sessions(days, agents=("claude",)):
        for r in s.requests:
            for use in r.tool_uses:
                m = re.match(r"mcp__(.+?)__", use.name)
                if m:
                    mcp_calls[m.group(1)] += 1
                elif use.name == "Skill" and isinstance(use.input.get("skill"), str):
                    skill_calls[use.input["skill"].split(":")[-1]] += 1
    total = sum(f["tokens"] for f in files)
    return {
        "days": days, "directory": str(cwd), "instruction_files": files, "instruction_tokens": total,
        "warn": total > cfg["context_warn_tokens"],
        "mcp_servers": [{"name": n, "source": src, "calls": mcp_calls[n]} for n, src in servers.items()],
        "skills": [{"name": n, "source": src, "calls": skill_calls[n]} for n, src in skills.items()],
    }


def format_context(d: dict) -> str:
    out = [f"toknix context ({d['directory']})", "", "Instruction files loaded every session"]
    out += [f"  {fmt_num(f['tokens']):>7}  {f['path']}" for f in d["instruction_files"]] or ["  none"]
    warn = f"  ⚠ over {fmt_num(load()['context_warn_tokens'])}: trim them" if d["warn"] else ""
    out.append(f"  {fmt_num(d['instruction_tokens']):>7}  total{warn}")
    for title, key in (("MCP servers", "mcp_servers"), ("Skills", "skills")):
        out += ["", f"{title} (calls in the last {d['days']} days)"]
        rows = d[key]
        out += [f"  {x['calls']:>5}  {x['name']}  ({x['source']})"
                + (f"  — not called in {d['days']} days: consider disabling it" if not x["calls"] else "")
                for x in rows] or ["  none"]
    return "\n".join(out)


# --------------------------------------------------------------------------- snapshot
SNAPSHOT_LABEL = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")


def snapshot(label: str, days: int) -> Path:
    cfg = load()
    rep = report_data(days, cfg, calibrate=False)
    waste = waste_data(days, cfg)
    data = {"label": label, "ts": time.time(), "days": days,
            "report": {k: rep[k] for k in ("sessions", "requests", "tokens", "etu", "usd", "etu_per_session",
                                           "cache_hit", "output_share", "tool_output_tokens", "output_breakdown")},
            "waste": {name: len(g["items"]) for name, g in waste["groups"].items()}}
    data["waste"]["cache_break_etu_lost"] = waste["groups"]["cache_breaks"]["etu_lost"]
    path = toknix_home() / "results" / f"snapshot-{label}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- for gain (N1.1)
def bash_stats(days: int, cfg: dict | None = None) -> dict:
    """R values of Bash results (requests left after each), the ETU each token of them cost (w_cw + w_cr·R at the
    carrying request's price, P8-T2) and how many Bash calls toknix wrapped: the command holds `toknix run`, or
    its result carries toknix's footer (whichever the transcript keeps)."""
    pr = pricing.Pricer(cfg or load())
    rs, multipliers, calls, wrapped = [], [], 0, 0
    for s in transcripts.sessions(days):
        for res, use, req, left in results(s):
            if use is not None and use.name in ("Bash", "shell"):
                rs.append(left)
                multipliers.append(pr.reread(req, 1, left))
        for r in s.requests:
            for use in r.tool_uses:
                if use.name not in ("Bash", "shell"):
                    continue
                calls += 1
                cmd = use.input.get("command")
                cmd = " ".join(cmd) if isinstance(cmd, list) else cmd if isinstance(cmd, str) else ""
                if re.search(r"tok(?:nix|opt) run", cmd) or (use.result and re.search(r"\[tok(?:nix|opt): ",
                                                                                         use.result.text)):
                    wrapped += 1
    return {"r_values": rs, "multipliers": multipliers, "bash_calls": calls, "wrapped": wrapped}


# --------------------------------------------------------------------------- CLI glue
def run(command: str, days: int | None, as_json: bool, directory: str | None = None, label: str | None = None) -> int:
    if command == "snapshot":
        if not label or not SNAPSHOT_LABEL.match(label):
            print("toknix snapshot: --label must be letters, digits, '.', '_' or '-'", file=sys.stderr)
            return 2
        path = snapshot(label, days or 7)
        write_out(f"saved {path}\n")
        return 0
    if command == "report":
        d = report_data(days or 7)
        text = format_report(d)
    elif command == "waste":
        d = waste_data(days or 7)
        text = format_waste(d)
    else:
        d = context_data(days or 30, directory)
        text = format_context(d)
    write_out((json.dumps(d, indent=2, default=str) if as_json else text) + "\n")
    return 0
