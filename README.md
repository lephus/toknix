# tokopt

**Cut the tokens AI coding agents waste, and prove it.** · [Tiếng Việt](README.vi.md)

tokopt is a command-line tool (Python ≥ 3.9, standard library only) for Claude Code, Codex CLI and
Gemini CLI. It does three things:

1. **Cuts waste:** noisy command output, reads of generated files, whole-file reads where one function would do.
2. **Measures what it cut**, from its own records and from the agent's transcripts.
3. **Proves quality did not drop**, with A/B runs of a real agent on your own tasks.

Nothing leaves your machine. Everything tokopt keeps is in `~/.tokopt/`.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh
```

On Windows (PowerShell, with Git for Windows installed):

```powershell
irm https://raw.githubusercontent.com/lephus/toknix/main/install.ps1 | iex
```

The installer:

- uses `uv tool install` if you have [uv](https://docs.astral.sh/uv/). Otherwise it creates a private
  venv in `~/.tokopt/venv`, so a system Python marked "externally managed" (PEP 668, e.g. Ubuntu 24.04) is
  never touched, and links `~/.local/bin/tokopt` to it;
- runs `tokopt install` and then `tokopt doctor`.

| Want to | Run |
|---|---|
| see what would happen | `curl -fsSL …/install.sh \| sh -s -- --dry-run` |
| install the command only, no hooks | `… \| sh -s -- --no-hooks` |
| remove everything | `… \| sh -s -- --uninstall`, or `tokopt uninstall` to drop only the hooks and rules |
| install without the script | `uv tool install git+https://github.com/lephus/toknix && tokopt install` |

On Windows, `irm | iex` cannot pass options. Set `$env:TOKOPT_DRY_RUN = "1"` or
`$env:TOKOPT_UNINSTALL = "1"` first.

### Update

Run the install command again:

```bash
curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh
```

- It reinstalls tokopt from the latest `main`, even when the version number did not change.
- Then `tokopt install` points the hooks at the new install and replaces the rules block with the new one.
  It never adds a second copy.
- Check with `tokopt --version` and `tokopt doctor`. `doctor` also says when the rules block is older than
  the installed tokopt.

To stay on a given release instead of `main`:
`curl -fsSL …/install.sh | TOKOPT_REF=v0.1.0 sh`.

**Publishing a new version**:
1. Raise `__version__` in `src/tokopt/__init__.py` and add an entry to `CHANGELOG.md`.
2. Push to `main`.
3. Tag the release (`git tag v0.1.1 && git push origin v0.1.1`) so that it can be pinned.

### Uninstall

Run the same script with `--uninstall`:

```bash
curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh -s -- --uninstall
```

It does three things:

1. Runs `tokopt uninstall`. This removes the 3 hooks and the statusLine from `~/.claude/settings.json`, and
   the rules block from `~/.claude/CLAUDE.md` (and from `AGENTS.md` / `GEMINI.md` if they have one). Those
   files go back to exactly what they were before the install.
2. Removes the `tokopt` command itself: `uv tool uninstall tokopt`, or the `~/.tokopt/venv` venv and the
   `~/.local/bin/tokopt` link.
3. Keeps your data in `~/.tokopt` (stats, saved outputs). To delete it too: `rm -rf ~/.tokopt`.

Then **restart Claude Code** so it stops loading the old hooks.

| Want to | Run |
|---|---|
| see what would be removed | `curl -fsSL …/install.sh \| sh -s -- --uninstall --dry-run` |
| turn tokopt off in Claude Code but keep the command | `tokopt uninstall` (turn it back on with `tokopt install`) |
| uninstall on Windows (PowerShell) | `$env:TOKOPT_UNINSTALL = "1"; irm https://raw.githubusercontent.com/lephus/toknix/main/install.ps1 \| iex` |

### What `tokopt install` changes

`~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`):

| Hook | Command |
|---|---|
| PreToolUse `Bash` | `tokopt hook bash` |
| PreToolUse `Read\|Grep\|Glob` | `tokopt hook read` |
| SessionStart `compact\|clear` | `tokopt hook session-start` |

It also adds `statusLine` → `tokopt statusline`, but only if you have no status line yet. All your other
settings stay as they are.

The rules block goes, between `<!-- tokopt:begin v1 -->` and `<!-- tokopt:end -->` markers, into
`~/.claude/CLAUDE.md`. It also goes into `~/.codex/AGENTS.md` and `~/.gemini/GEMINI.md`, but only if Codex
or Gemini CLI is installed.

Every change is backed up to `~/.tokopt/backups/` first. `tokopt uninstall` gives the files back byte for
byte. Restart Claude Code after installing, then run `tokopt doctor`.

## What it does in a session

- **Command output:** the Bash hook rewrites noisy commands (tests, builds, installs, git, listings) to
  `tokopt run -- '<cmd>'`.
  - The agent gets the failures, the summary and the exit code. Passing tests, progress bars and
    download logs are dropped.
  - **Error lines are never cut.**
  - When something was cut, the output ends with
    `[tokopt: 84 → 37 lines; full output: tokopt recall 851e91; exit 1]`.
  - `tokopt recall <id>` prints the original byte for byte. `--grep` and `--lines` print just a part.
  - `TOKOPT_RAW=1 <cmd>` is never rewritten.
- **Read guard:** stops three kinds of read, and always says how to read instead.
  - A whole generated, vendored or lock file (`node_modules/`, `package-lock.json`, `.min.js`…). Reading a
    slice with offset/limit is still allowed.
  - A whole file over 800 lines. The hint suggests `tokopt outline`.
  - The third identical read of an unchanged file. This resets after `/compact` or `/clear`.

  It also adds `head_limit: 200` to content Greps that have none.
- **Code by name:** `tokopt outline`, `symbol`, `refs` and `map` work on Python (exact, via `ast`) and on
  JS/TS, Go, Java, Rust, PHP, Ruby, Dart, Kotlin and C# (regex, with strings and comments masked).
- **Rules block:** tells the agent to use these tools, to answer with the diff instead of a summary, to
  Edit rather than rewrite files, and to keep its full reasoning for debugging, security and migrations.
  It is 282 tokens.

## Commands

| Command | What it does |
|---|---|
| `tokopt run -- "<cmd>"` | run a command, print filtered output, keep the original recallable; keeps the exit code |
| `tokopt recall <id> [--grep RE] [--lines A:B]` | print the full output of a filtered run |
| `tokopt gain [--days N] [--json]` | tokens saved by the filters and the read guard |
| `tokopt outline <file>` | symbols of a file with line ranges (`L12-40  def foo(a, b)`) |
| `tokopt symbol <name> [--kind K] [--path DIR]` | print a function or class (`Class.method` works) with line numbers |
| `tokopt refs <name> [--path DIR]` | where a name is used in code (not in strings or comments), definition left out |
| `tokopt map [DIR] [--budget 1500]` | the most used files and their top-level symbols, within a token budget |
| `tokopt report [--days 7] [--json]` | token use from transcripts: ETU, cache hit, output share, top sessions |
| `tokopt waste [--days 7] [--json]` | 7 groups of waste (re-read cost, big outputs, junk reads, cache breaks…) with fixes |
| `tokopt context [--days 30] [--path DIR] [--json]` | instruction files loaded every session, MCP servers and skills, and how often they are used |
| `tokopt snapshot --label L [--days 7]` | save a report summary to compare later |
| `tokopt bench tasks.json --label L [--rules FILE]` | A/B runs of a real agent in throwaway git worktrees |
| `tokopt compare A B [--json]` | compare two bench labels and give a verdict |
| `tokopt statusline` | Claude Code status line: `ctx 46% ▮▮▮▮▯▯▯▯▯▯ · 1.2M ETU · saved ~84k · /clear at 60%` |
| `tokopt install` / `uninstall` / `doctor` | set up, remove, and check the hooks and rules |
| `tokopt init-project [--path DIR]` | deny Read of junk directories in the project's `.claude/settings.json`; write `.geminiignore` |

**ETU** is the unit used to compare costs: `input + 1.25 × cache_write + 0.1 × cache_read + 5 × output`,
the ratio of Anthropic's list prices. Codex and Gemini price differently, so set `weights` in the config
for them.

## Reading `tokopt gain`

```
tokopt gain (all time)

Saved directly: 15.1k tokens (filters 15.1k, read guard 0)
With re-reads (estimate): 49.1k ETU = direct × 3.25 (w_cw 1.25 + w_cr 0.1 × R̄ 20)
  R̄ = 20 is the default value, not measured from transcripts yet

By filter kind
  kind          runs  cuts   saved  recalled  rate
  generic          3     2   15.1k         0    0%
```

- **Saved directly** is what the agent did not receive: original output minus filtered output, plus the
  files the read guard kept out. These are estimated tokens. Treat them as an estimate, not a bill.
- **With re-reads** is closer to what you pay. Tokens that enter the context are written to the cache
  once, then read again by every later request of the session.
  - R̄ is the median number of requests left after a Bash result, measured from your transcripts.
  - Until there are transcripts, R̄ is 20, and the line says it is the default.
- **cuts / recalled / rate:** how often each filter cut output, and how often you or the agent then
  needed `tokopt recall`.
  - Above 20% with 10 cuts or more, gain warns that the filter may cut too much. Please report the
    command when that happens.
- **Bash calls wrapped** is the share of Bash calls that went through `tokopt run`.
- A run whose error lines alone exceed the token budget is counted as `error_overflow`. Those error
  lines are still all kept.

## Reading `tokopt compare`

`tokopt bench` runs each task of a `tasks.json` several times, each time in a fresh `git worktree` that is
removed afterwards, even on timeout:

```json
{"repo": ".", "ref": "HEAD", "agent": "claude", "repeat": 3, "timeout": 900,
 "agent_args": ["--permission-mode", "acceptEdits"],
 "tasks": [{"id": "fix-cart", "prompt": "Fix the failing cart tests.", "check": "npm test"}]}
```

Run it once without tokopt and once with it, then compare:

```
tokopt compare baseline → v01 (12 vs 12 runs, 3 common tasks)

  pass rate             100% → 100%
  median ETU            37,151 → 24,606  -33.8% (95% CI -36.3% … -31.2%)
  median output tokens  2,972 → 1,712  -42.4% (95% CI -44.6% … -40.2%)
  ...
verdict: ACCEPT
output verdict: ACCEPT
```

| Verdict | When |
|---|---|
| **ACCEPT** | the pass rate did not drop, median ETU fell by at least 10%, and the whole 95% confidence interval (bootstrap, 2,000 resamples) is below zero |
| **REJECT** | the pass rate dropped, or B is not cheaper |
| **INCONCLUSIVE** | B is cheaper, but not clearly: run more repeats |

The **output verdict** applies the same rule to output tokens only. It is how a rules variant
(`bench --rules variant.md`) is chosen.

Only tasks present in both labels are compared. The example above comes from the fake agent in the tests.
Real runs cost real money, which is why you run them.

## Configuration

Settings live in `~/.tokopt/config.json`. Only the keys you set override the defaults, which are in
`src/tokopt/config.py`. The ones you are most likely to change:

| Key | Default | Meaning |
|---|---|---|
| `max_lines`, `max_tokens` | 120, 3000 | budget of a filtered output (doubled when the command fails) |
| `wrap_programs` | test runners, package managers, git, docker… | commands the Bash hook rewrites |
| `guard_junk`, `guard_big_file_lines`, `guard_repeat_reads` | true, 800, 2 | read guard rules (`0` turns a rule off) |
| `grep_head_limit` | 200 | added to content Greps (`0` = off) |
| `context_window` | 200000 | for the status line; 1M models are recognised |
| `weights` | `[1, 1.25, 0.1, 5]` | ETU weights: input, cache write, cache read, output |
| `hook_permission` | `null` | see the warning below |

| Environment variable | Effect |
|---|---|
| `TOKOPT_RAW=1` | `tokopt run` prints the raw output |
| `TOKOPT_DEBUG=1` | hooks print their errors to stderr. By default they never block the agent and stay silent |
| `TOKOPT_HOME` | where tokopt keeps its data (default `~/.tokopt`) |

### ⚠ `hook_permission`

By default the Bash hook only rewrites the command. Claude Code then asks for permission as it would for
the original command.

Setting `"hook_permission": "allow"` makes the hook approve every command it rewrites. That includes
`git push --force` and anything else your permission rules would have stopped. Leave it unset unless you
know you want that. `"ask"` always asks.

## Known limitations

- **Permission rules may not match rewritten commands.** A rule like `Bash(npm test:*)` may not match
  `tokopt run -- 'npm test'`, so Claude Code can ask where it did not before. This has not been checked on
  Claude Code yet; it is check B of [docs/VERIFY.md](docs/VERIFY.md).
- **Token numbers are estimates.**
  - tokopt counts characters per class: about 4 per token for text, 2 for hex/base64, 1.5 for non-ASCII.
  - It can calibrate these ratios from your transcripts. On the transcripts tested so far, the fit missed
    its quality bar (median error 21–25% against the 15% required), so the defaults are used.
  - `gain` therefore shows estimates. `report` and `waste` use the exact `usage` numbers of the transcripts.
- **Codex and Gemini CLI have no hooks yet.** They get the rules block, so the agent can call `tokopt run`
  and `tokopt outline` itself. Gemini transcripts are not read.
- **Some Claude Code behaviour is still to be confirmed on a real session**
  ([docs/VERIFY.md](docs/VERIFY.md)):
  - whether `updatedInput` is applied without a permission decision;
  - whether Claude Code already skips repeated Reads;
  - whether a Grep cut by `head_limit` tells the agent it was cut;
  - whether the `init-project` deny rules match.
- **Windows** is supported through Git Bash, as Claude Code itself is. CI runs the tests and the installer
  on Windows, but no one has used tokopt with Claude Code on Windows yet.
- **Outline:**
  - For languages other than Python it is regex-based. Minified code can confuse it.
  - Swift, C and C++ are not supported; `outline` prints the first 200 lines of those files.
  - `map` counts a name in strings and comments as a use.
- **The "Bash calls wrapped" share in `gain` is approximate.** It counts commands that hold `tokopt run`,
  or whose output carries tokopt's footer.

## Development

```bash
python -m unittest discover -s tests -v        # standard library only, no pytest needed
python scripts/bench_codesearch.py 2000        # symbol/refs on a generated 2,000-file repo
```

- [docs/PLAN.md](docs/PLAN.md): what is built, in what order.
- [docs/ALGORITHMS.md](docs/ALGORITHMS.md): how each part works. It is the reference: when it and the code
  disagree, the document is fixed first.
- [docs/VERIFY.md](docs/VERIFY.md): the checks on a real Claude Code session.

## License

MIT, see [LICENSE](LICENSE).
