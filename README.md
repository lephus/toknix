# toknix

**Cut the tokens AI coding agents waste, and prove it.** · [Tiếng Việt](README.vi.md)

toknix is a command-line tool (Python ≥ 3.9, standard library only) for Claude Code, Codex CLI and
Gemini CLI. It does three things:

1. **Cuts waste:** noisy command output, reads of generated files, whole-file reads where one function would do.
2. **Measures what it cut**, from its own records and from the agent's transcripts.
3. **Proves quality did not drop**, with A/B runs of a real agent on your own tasks.

Nothing leaves your machine. Everything toknix keeps is in `~/.toknix/`.

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
  venv in `~/.toknix/venv`, so a system Python marked "externally managed" (PEP 668, e.g. Ubuntu 24.04) is
  never touched, and links `~/.local/bin/toknix` to it;
- puts `~/.local/bin` on PATH in your shell profile (`~/.zshrc`, `~/.bash_profile` or `~/.bashrc`,
  `config.fish`, `~/.profile`) when it is not there yet: two marked lines, never added twice. Open a new
  terminal afterwards, or run the `export` line the installer prints at the end;
- runs `toknix install` and then `toknix doctor`.

| Want to | Run |
|---|---|
| see what would happen | `curl -fsSL …/install.sh \| sh -s -- --dry-run` |
| install the command only, no hooks | `… \| sh -s -- --no-hooks` |
| leave my shell profile alone | `… \| sh -s -- --no-modify-path` |
| remove everything | `… \| sh -s -- --uninstall`, or `toknix uninstall` to drop only the hooks and rules |
| install without the script | `uv tool install git+https://github.com/lephus/toknix && toknix install` |

On Windows, `irm | iex` cannot pass options. Set `$env:TOKNIX_DRY_RUN = "1"` or
`$env:TOKNIX_UNINSTALL = "1"` first.

### Update

Run the install command again:

```bash
curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh
```

- It reinstalls toknix from the latest `main`, even when the version number did not change.
- Then `toknix install` points the hooks at the new install and replaces the rules block with the new one.
  It never adds a second copy.
- Check with `toknix --version` and `toknix doctor`. `doctor` also says when the rules block is older than
  the installed toknix.

To stay on a given release instead of `main`:
`curl -fsSL …/install.sh | TOKNIX_REF=v0.2.1 sh`.

**Publishing a new version**:
1. Raise `__version__` in `src/toknix/__init__.py` and add an entry to `CHANGELOG.md`.
2. Push to `main`.
3. Tag the release (`git tag v0.2.2 && git push origin v0.2.2`) so that it can be pinned.

### Uninstall

Run the same script with `--uninstall`:

```bash
curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh -s -- --uninstall
```

It does three things:

1. Runs `toknix uninstall`. This removes the 4 hooks and the statusLine from `~/.claude/settings.json`, and
   the rules block from `~/.claude/CLAUDE.md` (and from `AGENTS.md` / `GEMINI.md` if they have one). Those
   files go back to exactly what they were before the install.
2. Removes the `toknix` command itself: `uv tool uninstall toknix`, or the `~/.toknix/venv` venv and the
   `~/.local/bin/toknix` link, and the two PATH lines the installer added to your shell profile.
3. Keeps your data in `~/.toknix` (stats, saved outputs). To delete it too: `rm -rf ~/.toknix`.

Then **restart Claude Code** so it stops loading the old hooks.

| Want to | Run |
|---|---|
| see what would be removed | `curl -fsSL …/install.sh \| sh -s -- --uninstall --dry-run` |
| turn toknix off in Claude Code but keep the command | `toknix uninstall` (turn it back on with `toknix install`) |
| uninstall on Windows (PowerShell) | `$env:TOKNIX_UNINSTALL = "1"; irm https://raw.githubusercontent.com/lephus/toknix/main/install.ps1 \| iex` |

### What `toknix install` changes

`~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`):

| Hook | Command |
|---|---|
| PreToolUse `Bash` | `toknix hook bash` |
| PreToolUse `Read\|Grep\|Glob` | `toknix hook read` |
| SessionStart `compact\|clear` | `toknix hook session-start` |
| UserPromptSubmit | `toknix hook prompt`: warns you (not the model) when the context is over `context_budget` (1M) |
| PreToolUse `Agent\|Task` | `toknix hook agent`: gives a subagent a cheaper model by its task (Explore → haiku; read, review, implement a given task → sonnet; design, planning, debugging keep yours). Never routes up, never overrides a model you or the agent chose; `"route_subagents": false` turns it off |

It also adds `statusLine` → `toknix statusline`, but only if you have no status line yet. All your other
settings stay as they are.

The rules block goes, between `<!-- toknix:begin v1 -->` and `<!-- toknix:end -->` markers, into
`~/.claude/CLAUDE.md`. It also goes into `~/.codex/AGENTS.md` and `~/.gemini/GEMINI.md`, but only if Codex
or Gemini CLI is installed.

Every change is backed up to `~/.toknix/backups/` first. `toknix uninstall` gives the files back byte for
byte. Restart Claude Code after installing, then run `toknix doctor`.

## What it does in a session

- **Command output:** the Bash hook rewrites noisy commands (tests, builds, installs, git, listings) to
  `toknix run -- '<cmd>'`.
  - The agent gets the failures, the summary and the exit code. Passing tests, progress bars and
    download logs are dropped.
  - **Error lines are never cut.**
  - When something was cut, the output ends with
    `[toknix: 84 → 37 lines; full output: toknix recall 851e91; exit 1]`.
  - `toknix recall <id>` prints the original byte for byte. `--grep` and `--lines` print just a part.
  - `TOKNIX_RAW=1 <cmd>` is never rewritten.
- **Read guard:** stops three kinds of read, and always says how to read instead.
  - A whole generated, vendored or lock file (`node_modules/`, `package-lock.json`, `.min.js`…). Reading a
    slice with offset/limit is still allowed.
  - A whole file over 800 lines. The hint suggests `toknix outline`.
  - The third identical read of an unchanged file. This resets after `/compact` or `/clear`.

  It also adds `head_limit: 200` to content Greps that have none.
- **Code by name:** `toknix outline`, `symbol`, `refs` and `map` work on Python (exact, via `ast`) and on
  JS/TS, Go, Java, Rust, PHP, Ruby, Dart, Kotlin and C# (regex, with strings and comments masked).
- **Rules block:** tells the agent to use these tools, to answer with the diff instead of a summary, to
  Edit rather than rewrite files, and to keep its full reasoning for debugging, security and migrations.
  It is 282 tokens.

## Commands

| Command | What it does |
|---|---|
| `toknix run -- "<cmd>"` | run a command, print filtered output, keep the original recallable; keeps the exit code |
| `toknix recall <id> [--grep RE] [--lines A:B]` | print the full output of a filtered run |
| `toknix gain [--days N] [--json]` | tokens saved by the filters and the read guard |
| `toknix ui [--port N] [--no-open]` | a page on `http://127.0.0.1:8765` to watch toknix work: each filtered command with its original and filtered output side by side, read-guard denials, savings per day. Local only, read-only |
| `toknix outline <file>` | symbols of a file with line ranges (`L12-40  def foo(a, b)`) |
| `toknix symbol <name> [--kind K] [--path DIR]` | print a function or class (`Class.method` works) with line numbers |
| `toknix refs <name> [--path DIR]` | where a name is used in code (not in strings or comments), definition left out |
| `toknix map [DIR] [--budget 1500]` | the most used files and their top-level symbols, within a token budget |
| `toknix report [--days 7] [--json]` | token use from transcripts: ETU and $, cost by model, cache hit, output share, top sessions |
| `toknix waste [--days 7] [--json]` | 8 groups of waste (re-read cost, big outputs, junk reads, cache breaks, subagents by type and model…) with fixes |
| `toknix context [--days 30] [--path DIR] [--json]` | instruction files loaded every session, MCP servers and skills, how often they are used, and the fixed overhead of every request (tools, skill listing, MCP…) with what unused plugins, MCP servers and skills cost |
| `toknix snapshot --label L [--days 7]` | save a report summary to compare later |
| `toknix bench tasks.json --label L [--rules FILE]` | A/B runs of a real agent in throwaway git worktrees |
| `toknix compare A B [--json]` | compare two bench labels and give a verdict |
| `toknix statusline` | Claude Code status line: `ctx 92k/1.0M ▯▯▯▯▯▯▯▯▯▯ · 1.2M ETU · saved ~84k ETU (6.5%) · /clear at 600k` |
| `toknix install` / `uninstall` / `doctor` | set up, remove, and check the hooks and rules |
| `toknix init-project [--path DIR]` | deny Read of junk directories in the project's `.claude/settings.json`; write `.geminiignore` |

**ETU** is the unit used to compare costs: one input token of `etu_model` (Claude Opus 5.5, $4 per million
tokens). Each request is priced at its own model's list price: input, cache writes (5-minute and 1-hour),
cache reads and output. So ETU adds up across models and is proportional to dollars; reports show both. For
Opus 5.5 that is `input + 1.25 × 5-min cache write + 2 × 1-hour cache write + 0.05 × cache read + 5 × output`.
The dollars are API list prices: on a Pro or Max plan you do not pay per token, so read them as a comparison.
A model without a price (Codex, Gemini) uses `weights` and has no $; set them for those agents.

## Reading `toknix gain`

```
toknix gain (all time)

Saved directly: 15.1k tokens (filters 15.1k, read guard 0)
With re-reads (estimate): 34.0k ETU ≈ $0.136 = direct × 2.25 (w_cw 1.25 + w_cr 0.05 × R̄ 20, claude-opus-5-5 prices)
  R̄ = 20 is the default value, not measured from transcripts yet

By filter kind
  kind          runs  cuts   saved  recalled  rate
  generic          3     2   15.1k         0    0%
```

- **Saved directly** is what the agent did not receive: original output minus filtered output, plus the
  files the read guard kept out. These are estimated tokens. Treat them as an estimate, not a bill.
- **With re-reads** is closer to what you pay. Tokens that enter the context are written to the cache
  once, then read again by every later request of the session.
  - R̄ is the median number of requests left after a Bash result, measured from your transcripts. Each
    result is priced at the model of the request that carried it.
  - Until there are transcripts, R̄ is 20 at Opus 5.5 prices, and the line says it is the default.
- **cuts / recalled / rate:** how often each filter cut output, and how often you or the agent then
  needed `toknix recall`.
  - Above 20% with 10 cuts or more, gain warns that the filter may cut too much. Please report the
    command when that happens.
- **Bash calls wrapped** is the share of Bash calls that went through `toknix run`.
- A run whose error lines alone exceed the token budget is counted as `error_overflow`. Those error
  lines are still all kept.

## Reading `toknix compare`

`toknix bench` runs each task of a `tasks.json` several times, each time in a fresh `git worktree` that is
removed afterwards, even on timeout:

```json
{"repo": ".", "ref": "HEAD", "agent": "claude", "repeat": 3, "timeout": 900,
 "agent_args": ["--permission-mode", "acceptEdits"],
 "tasks": [{"id": "fix-cart", "prompt": "Fix the failing cart tests.", "check": "npm test"}]}
```

Run it once without toknix and once with it, then compare:

```
toknix compare baseline → v01 (12 vs 12 runs, 3 common tasks)

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

Settings live in `~/.toknix/config.json`. Only the keys you set override the defaults, which are in
`src/toknix/config.py`. The ones you are most likely to change:

| Key | Default | Meaning |
|---|---|---|
| `max_lines`, `max_tokens` | 120, 3000 | budget of a filtered output (doubled when the command fails) |
| `wrap_programs` | test runners, package managers, git, docker… | commands the Bash hook rewrites |
| `guard_junk`, `guard_big_file_lines`, `guard_repeat_reads` | true, 800, 2 | read guard rules (`0` turns a rule off) |
| `grep_head_limit` | 200 | added to content Greps (`0` = off) |
| `context_window` | 1000000 | for the status line; 1M models are recognised |
| `etu_model` | `"claude-opus-5-5"` | 1 ETU = one input token of this model (`""`: every request uses `weights`) |
| `prices` | Anthropic list prices | $ per million tokens by model: input, 5-min cache write, 1-hour cache write, cache read, output. Your entries add to the defaults |
| `weights` | `[1, 1.25, 0.1, 5]` | ETU weights of a model without a price: input, cache write, cache read, output |
| `hook_permission` | `null` | see the warning below |

| Environment variable | Effect |
|---|---|
| `TOKNIX_RAW=1` | `toknix run` prints the raw output |
| `TOKNIX_DEBUG=1` | hooks print their errors to stderr. By default they never block the agent and stay silent |
| `TOKNIX_HOME` | where toknix keeps its data (default `~/.toknix`) |

### ⚠ `hook_permission`

By default the Bash hook only rewrites the command. Claude Code then asks for permission as it would for
the original command.

Setting `"hook_permission": "allow"` makes the hook approve every command it rewrites. That includes
`git push --force` and anything else your permission rules would have stopped. Leave it unset unless you
know you want that. `"ask"` always asks.

## Known limitations

- **Permission rules may not match rewritten commands.** A rule like `Bash(npm test:*)` may not match
  `toknix run -- 'npm test'`, so Claude Code can ask where it did not before. This has not been checked on
  Claude Code yet; it is check B of [docs/VERIFY.md](docs/VERIFY.md).
- **Token numbers are estimates.**
  - toknix counts characters per class: about 4 per token for text, 2 for hex/base64, 1.5 for non-ASCII.
  - It can calibrate these ratios from your transcripts. On the transcripts tested so far, the fit missed
    its quality bar (median error 21–25% against the 15% required), so the defaults are used.
  - `gain` therefore shows estimates. `report` and `waste` use the exact `usage` numbers of the transcripts.
- **Codex and Gemini CLI have no hooks yet.** They get the rules block, so the agent can call `toknix run`
  and `toknix outline` itself. Gemini transcripts are not read.
- **Some Claude Code behaviour is still to be confirmed on a real session**
  ([docs/VERIFY.md](docs/VERIFY.md)):
  - whether `updatedInput` is applied without a permission decision;
  - whether Claude Code already skips repeated Reads;
  - whether a Grep cut by `head_limit` tells the agent it was cut;
  - whether the `init-project` deny rules match.
- **Windows** is supported through Git Bash, as Claude Code itself is. CI runs the tests and the installer
  on Windows, but no one has used toknix with Claude Code on Windows yet.
- **Outline:**
  - For languages other than Python it is regex-based. Minified code can confuse it.
  - Swift, C and C++ are not supported; `outline` prints the first 200 lines of those files.
  - `map` counts a name in strings and comments as a use.
- **The "Bash calls wrapped" share in `gain` is approximate.** It counts commands that hold `toknix run`,
  or whose output carries toknix's footer.

## Development

```bash
python -m unittest discover -s tests -v        # standard library only, no pytest needed
python scripts/bench_codesearch.py 2000        # symbol/refs on a generated 2,000-file repo
```

- [docs/PLAN.md](docs/PLAN.md): what is built, in what order.
- [docs/ALGORITHMS.md](docs/ALGORITHMS.md): how each part works. It is the reference: when it and the code
  disagree, the document is fixed first.
- [docs/VERIFY.md](docs/VERIFY.md): the checks on a real Claude Code session.

## References

Both papers train a new tokenizer. toknix does not: you pay for the tokens your provider's tokenizer counts.

- Shi et al., *Counting and Min-Cost Encoding for Tokenization in Large Language Models* (CNF-MCE),
  [arXiv:2610.01127](https://arxiv.org/abs/2610.01127), 1 October 2026. It counts substrings across the
  whole corpus, keeps only the ones that are actually used, then picks the segmentation with the lowest
  total cost, and compares this with BPE. toknix takes two ideas from it: token cost depends on the kind of
  text, not on its length in characters (token estimates by character class, rewriting costly strings), and
  what matters is what gets used, not what appears often (filter recall, MCP servers and skills that are
  loaded but never called).
- Reif, Kaplan, Schwartz, *More Than Words: Compositional Tokenization for Efficient Language Models*
  (CoBPE), [arXiv:2610.05597](https://arxiv.org/abs/2610.05597), October 2026. It writes a short phrase as
  a base token plus modifiers, which makes sequences about 30% shorter than with BPE.

## License

MIT, see [LICENSE](LICENSE).
