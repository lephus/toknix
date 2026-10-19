"""`toknix bench` and `toknix compare` (A6.3): A/B runs of a real agent on real tasks, and the verdict.

tasks.json: {repo, ref, agent, repeat, timeout, setup[], agent_args?, agent_cmd?, tasks:[{id, prompt, check, agent_args?}]}
Each task × repeat runs in a fresh `git worktree` (always removed, even after a timeout), and its result is
one JSON line in ~/.toknix/results/bench-<label>.jsonl.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from toknix import installer
from toknix.config import claude_dir, load, toknix_home
from toknix.runner import shell_argv, write_out

LABEL = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")
AGENT_CMD = {"claude": ["claude"], "codex": ["codex"], "gemini": ["gemini"]}


class BenchError(Exception):
    pass


def results_path(label: str) -> Path:
    return toknix_home() / "results" / f"bench-{label}.jsonl"


# --------------------------------------------------------------------------- tasks
def load_tasks(path: Path) -> dict:
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BenchError(f"cannot read {path}: {e}") from None
    if not isinstance(spec, dict) or not isinstance(spec.get("tasks"), list) or not spec["tasks"]:
        raise BenchError(f"{path}: needs a non-empty \"tasks\" list")
    if not spec.get("repo"):
        raise BenchError(f"{path}: needs \"repo\" (the git repository to run in)")
    for t in spec["tasks"]:
        if not isinstance(t, dict) or not t.get("id") or not t.get("prompt") or not t.get("check"):
            raise BenchError(f"{path}: every task needs \"id\", \"prompt\" and \"check\"")
    agent = spec.get("agent", "claude")
    if agent not in AGENT_CMD:
        raise BenchError(f"{path}: agent must be one of {', '.join(AGENT_CMD)}")
    return spec


def _argv(cmd) -> list[str]:
    return cmd if isinstance(cmd, list) else shell_argv(cmd)


def agent_argv(spec: dict, task: dict) -> list[str]:
    """`claude -p PROMPT --output-format json`, `codex exec --json --full-auto PROMPT`, `gemini -p PROMPT
    --output-format json` (A6.3), with spec/task agent_args; agent_cmd replaces the program (tests, wrappers)."""
    agent = spec.get("agent", "claude")
    prog = list(spec.get("agent_cmd") or AGENT_CMD[agent])
    extra = list(task.get("agent_args", spec.get("agent_args", [])))
    prompt = task["prompt"]
    if agent == "codex":
        return prog + ["exec", "--json", "--full-auto", *extra, prompt]
    return prog + ["-p", prompt, "--output-format", "json", *extra]


# --------------------------------------------------------------------------- agent output
def parse_usage(agent: str, stdout: str) -> dict:
    """{input, cache_write, cache_read, output, cost, turns} from the agent's JSON; missing values are 0."""
    out = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "cost": 0.0, "turns": 0}

    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    if agent == "codex":  # JSONL events; usage of every turn.completed added up [ASSUMPTION]
        for line in stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict):
                u = ev["usage"]
                cached = num(u.get("cached_input_tokens"))
                out["input"] += max(num(u.get("input_tokens")) - cached, 0)
                out["cache_read"] += cached
                out["output"] += num(u.get("output_tokens"))
                out["turns"] += 1
        return out
    try:
        data = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
    except ValueError:
        return out
    if not isinstance(data, dict):
        return out
    if agent == "gemini":  # stats.models.*.tokens [ASSUMPTION]
        for m in ((data.get("stats") or {}).get("models") or {}).values():
            tok = (m or {}).get("tokens") or {}
            cached = num(tok.get("cached"))
            out["input"] += max(num(tok.get("prompt")) - cached, 0)
            out["cache_read"] += cached
            out["output"] += num(tok.get("candidates"))
        return out
    u = data.get("usage") if isinstance(data.get("usage"), dict) else {}  # claude -p: FACT
    out.update(input=num(u.get("input_tokens")), cache_write=num(u.get("cache_creation_input_tokens")),
               cache_read=num(u.get("cache_read_input_tokens")), output=num(u.get("output_tokens")),
               cost=float(num(data.get("total_cost_usd"))), turns=num(data.get("num_turns")))
    return out


def _run(argv: list[str], cwd: Path, timeout: float, env: dict) -> tuple[int | None, str, bool]:
    """(exit code, stdout, timed out). The whole process group is killed on timeout, children included."""
    kwargs = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    try:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, **kwargs)
    except OSError as e:
        raise BenchError(f"cannot start {argv[0]}: {e}") from None
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out.decode("utf-8", errors="replace"), False
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(p.pid, signal.SIGKILL)
            else:
                p.kill()
        except OSError:
            pass
        out, _ = p.communicate()
        return None, out.decode("utf-8", errors="replace"), True


# --------------------------------------------------------------------------- bench
def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def global_rules_present() -> bool:
    try:
        return bool(installer.RULES_BLOCK_RE.search((claude_dir() / "CLAUDE.md").read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        return False


def insert_rules(worktree: Path, rules: str, agent: str):
    """The rules block in the worktree's instruction file, between the A8 markers."""
    name = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "gemini": "GEMINI.md"}[agent]
    f = worktree / name
    text = f.read_text(encoding="utf-8") if f.exists() else ""
    block = f"<!-- toknix:begin bench -->\n{rules.rstrip()}\n<!-- toknix:end -->\n"
    m = installer.RULES_BLOCK_RE.search(text)
    text = text[:m.start()] + block + text[m.end():] if m else (text + ("\n" if text else "") + block)
    f.write_text(text, encoding="utf-8")


def run_bench(tasks_file: str, label: str, rules_file: str | None = None, repeat: int | None = None) -> Path:
    cfg = load()
    w = [float(x) for x in cfg["weights"]]
    spec = load_tasks(Path(tasks_file))
    repo = Path(spec["repo"]).expanduser().resolve()
    if _git(repo, "rev-parse", "--git-dir").returncode != 0:
        raise BenchError(f"{repo} is not a git repository")
    agent = spec.get("agent", "claude")
    rules = Path(rules_file).read_text(encoding="utf-8") if rules_file else None
    global_rules = global_rules_present()
    if rules is not None and global_rules:
        print("toknix bench: warning: your global CLAUDE.md already has a toknix rules block; it applies to both "
              "arms, so the comparison measures the worktree block on top of it", file=sys.stderr)
    out_path = results_path(label)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    reps = repeat or int(spec.get("repeat", 1))
    timeout = float(spec.get("timeout", 900))
    with open(out_path, "w", encoding="utf-8") as fh:
        for task in spec["tasks"]:
            for rep in range(reps):
                row = _one(spec, task, rep, repo, agent, rules, rules_file, global_rules, timeout, w, label)
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                note = " (timeout)" if row["timeout"] else ""
                if not row["timeout"] and not any(row["usage"].values()):  # the agent never really ran
                    note = f" (agent exited {row['agent_exit']}, no usage: check the agent command)"
                write_out(f"{label} {task['id']}#{rep}: {'pass' if row['passed'] else 'FAIL'}{note}, "
                          f"{row['etu']:,.0f} ETU, {row['wall']:.0f}s\n")
    _git(repo, "worktree", "prune")
    return out_path


def _one(spec, task, rep, repo, agent, rules, rules_file, global_rules, timeout, w, label) -> dict:
    parent = Path(tempfile.mkdtemp(prefix="toknix-bench-"))
    wt = parent / "wt"
    started = time.time()
    try:
        r = _git(repo, "worktree", "add", "--detach", str(wt), spec.get("ref", "HEAD"))
        if r.returncode != 0:
            raise BenchError(f"git worktree add failed: {r.stderr.strip()}")
        env = dict(os.environ, TOKNIX_BENCH_LABEL=label, TOKNIX_BENCH_TASK=str(task["id"]),
                   TOKNIX_BENCH_REPEAT=str(rep))
        for step in spec.get("setup", []):
            _run(_argv(step), wt, timeout, env)
        if rules is not None:
            insert_rules(wt, rules, agent)
        code, stdout, timed_out = _run(agent_argv(spec, task), wt, timeout, env)
        usage = parse_usage(agent, stdout)
        passed = False
        if not timed_out:
            check_code, _, _ = _run(_argv(task["check"]), wt, timeout, env)
            passed = check_code == 0
        tokens = {k: usage[k] for k in ("input", "cache_write", "cache_read", "output")}
        return {"label": label, "task": task["id"], "repeat": rep, "agent": agent, "passed": passed,
                "timeout": timed_out, "agent_exit": code, "usage": tokens,
                "etu": w[0] * tokens["input"] + w[1] * tokens["cache_write"] + w[2] * tokens["cache_read"]
                + w[3] * tokens["output"], "cost": usage["cost"], "turns": usage["turns"],
                "wall": time.time() - started, "rules": rules_file, "global_rules": global_rules, "ts": started}
    finally:
        _git(repo, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(parent, ignore_errors=True)


# --------------------------------------------------------------------------- compare
def load_results(label: str) -> list[dict]:
    path = results_path(label)
    if not path.exists():
        raise BenchError(f"no bench results for {label!r} ({path})")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and "task" in row:
            rows.append(row)
    return rows


def _bootstrap(a: list[float], b: list[float], n: int = 2000, seed: int = 42) -> tuple[float, float]:
    """95 % CI of median(B)/median(A) − 1, resampling each arm with replacement."""
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        ma = statistics.median(rng.choices(a, k=len(a)))
        mb = statistics.median(rng.choices(b, k=len(b)))
        if ma:
            stats.append(mb / ma - 1)
    stats.sort()
    if not stats:
        return 0.0, 0.0
    return stats[int(0.025 * (len(stats) - 1))], stats[int(0.975 * (len(stats) - 1))]


def _verdict(pass_a: float, pass_b: float, change: float, ci_high: float, cfg: dict) -> str:
    """ACCEPT: pass rate kept, ≥ min_saving cheaper, CI upper bound < 0. REJECT: pass rate down, or not
    cheaper. INCONCLUSIVE otherwise (A6.3)."""
    if pass_b < pass_a - cfg["bench_max_pass_drop"] or change >= 0:
        return "REJECT"
    if -change >= cfg["bench_min_saving"] and ci_high < 0:
        return "ACCEPT"
    return "INCONCLUSIVE"


def compare_data(label_a: str, label_b: str, cfg: dict | None = None) -> dict:
    cfg = cfg or load()
    a, b = load_results(label_a), load_results(label_b)
    common = sorted({r["task"] for r in a} & {r["task"] for r in b})
    if not common:
        raise BenchError(f"{label_a!r} and {label_b!r} have no task in common")
    a = [r for r in a if r["task"] in common]
    b = [r for r in b if r["task"] in common]

    def pass_rate(rows):
        return sum(1 for r in rows if r["passed"]) / len(rows)

    def side(rows, key):
        return [float(r["usage"]["output"]) if key == "output" else float(r[key]) for r in rows]

    pa, pb = pass_rate(a), pass_rate(b)
    ea, eb = side(a, "etu"), side(b, "etu")
    change = statistics.median(eb) / statistics.median(ea) - 1 if statistics.median(ea) else 0.0
    ci = _bootstrap(ea, eb)
    oa, ob = side(a, "output"), side(b, "output")
    out_change = statistics.median(ob) / statistics.median(oa) - 1 if statistics.median(oa) else 0.0
    out_ci = _bootstrap(oa, ob)
    tasks = []
    for t in common:
        ta = [r for r in a if r["task"] == t]
        tb = [r for r in b if r["task"] == t]
        tasks.append({"task": t, "runs": {"a": len(ta), "b": len(tb)},
                      "pass": {"a": pass_rate(ta), "b": pass_rate(tb)},
                      "median_etu": {"a": statistics.median(side(ta, "etu")), "b": statistics.median(side(tb, "etu"))},
                      "median_output": {"a": statistics.median(side(ta, "output")),
                                        "b": statistics.median(side(tb, "output"))}})
    return {
        "a": label_a, "b": label_b, "runs": {"a": len(a), "b": len(b)}, "pass_rate": {"a": pa, "b": pb},
        "median_etu": {"a": statistics.median(ea), "b": statistics.median(eb)}, "change": change, "ci": list(ci),
        "mean_cost": {"a": statistics.mean(side(a, "cost")), "b": statistics.mean(side(b, "cost"))},
        "median_wall": {"a": statistics.median(side(a, "wall")), "b": statistics.median(side(b, "wall"))},
        "verdict": _verdict(pa, pb, change, ci[1], cfg),
        "output": {"median": {"a": statistics.median(oa), "b": statistics.median(ob)}, "change": out_change,
                   "ci": list(out_ci), "verdict": _verdict(pa, pb, out_change, out_ci[1], cfg)},
        "global_rules": any(r.get("global_rules") for r in a + b),
        "no_usage": {"a": sum(1 for r in a if not any(r["usage"].values())),
                     "b": sum(1 for r in b if not any(r["usage"].values()))},
        "tasks": tasks,
    }


def format_compare(d: dict) -> str:
    def pct(x):
        return f"{x:+.1%}"

    out = [f"toknix compare {d['a']} → {d['b']} ({d['runs']['a']} vs {d['runs']['b']} runs, "
           f"{len(d['tasks'])} common tasks)", "",
           f"  {'pass rate':<22}{d['pass_rate']['a']:.0%} → {d['pass_rate']['b']:.0%}",
           f"  {'median ETU':<22}{d['median_etu']['a']:,.0f} → {d['median_etu']['b']:,.0f}  {pct(d['change'])} "
           f"(95% CI {pct(d['ci'][0])} … {pct(d['ci'][1])})",
           f"  {'median output tokens':<22}{d['output']['median']['a']:,.0f} → {d['output']['median']['b']:,.0f}  "
           f"{pct(d['output']['change'])} (95% CI {pct(d['output']['ci'][0])} … {pct(d['output']['ci'][1])})",
           f"  {'mean cost':<22}${d['mean_cost']['a']:.3f} → ${d['mean_cost']['b']:.3f}",
           f"  {'median wall time':<22}{d['median_wall']['a']:.0f}s → {d['median_wall']['b']:.0f}s", "",
           f"  {'task':<20}{'pass A→B':>12}{'ETU A':>12}{'ETU B':>12}{'out A':>10}{'out B':>10}"]
    for t in d["tasks"]:
        out.append(f"  {t['task'][:20]:<20}{t['pass']['a']:>5.0%} → {t['pass']['b']:<4.0%}"
                   f"{t['median_etu']['a']:>12,.0f}{t['median_etu']['b']:>12,.0f}"
                   f"{t['median_output']['a']:>10,.0f}{t['median_output']['b']:>10,.0f}")
    out += ["", f"verdict: {d['verdict']}", f"output verdict: {d['output']['verdict']}"]
    if d["global_rules"]:
        out.append("note: a global CLAUDE.md rules block was present during these runs")
    if d["no_usage"]["a"] or d["no_usage"]["b"]:
        out.append(f"warning: {d['no_usage']['a']} + {d['no_usage']['b']} runs have no usage (the agent did not "
                   f"run or its output was not understood); the verdict means little until they are fixed")
    return "\n".join(out)


# --------------------------------------------------------------------------- CLI glue
def bench_cmd(tasks_file: str, label: str, rules: str | None, repeat: int | None) -> int:
    if not LABEL.match(label):
        print("toknix bench: --label must be letters, digits, '.', '_' or '-'", file=sys.stderr)
        return 2
    try:
        path = run_bench(tasks_file, label, rules, repeat)
    except BenchError as e:
        print(f"toknix bench: {e}", file=sys.stderr)
        return 2
    write_out(f"results: {path}\n")
    return 0


def compare_cmd(a: str, b: str, as_json: bool) -> int:
    try:
        d = compare_data(a, b)
    except BenchError as e:
        print(f"toknix compare: {e}", file=sys.stderr)
        return 1
    write_out((json.dumps(d, indent=2) if as_json else format_compare(d)) + "\n")
    return 0
