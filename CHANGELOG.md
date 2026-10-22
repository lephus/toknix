# Changelog

## Unreleased

### Choosing models
- New hook `toknix hook agent` (PreToolUse `Agent|Task`). Subagents are about 70% of the cost of a Claude Code
  session. When a subagent is started without a model, toknix gives it a cheaper one by its task, as Anthropic
  recommends: Explore gets Haiku; reading, searching, reviewing and implementing a task that is already written
  down get Sonnet; design, planning and debugging keep the main session's model. It only ever picks a model
  cheaper than the main session's, never overrides a model that the agent, an agent definition or you chose,
  and leaves forks, Plan and plugin agents alone. `"route_subagents": false` turns it off; `route_models` and
  `route_agent_types` tune it. Run `toknix install` to add the hook; `toknix doctor` shows how many subagents
  it moved in the last 7 days.

### Changed
- `context_window` and `context_budget` default to 1,000,000 (was 200k): the status line counts against 1M and
  the context reminder only comes past 1M. Set `context_budget` lower in `~/.toknix/config.json` for an earlier
  reminder.

## 0.2.1 (2026-10-21)

### Fixed
- `install.sh`: on a fresh macOS the install succeeded but `toknix` was "command not found", because
  `~/.local/bin` is not on PATH there and the script only printed a note. It now adds `~/.local/bin` to PATH
  in your shell profile (two marked lines; `--no-modify-path` to skip), ends with the `export` line to use
  in the current terminal, and `--uninstall` takes those lines out again.

## 0.2.0 (2026-10-21)

Costs are now counted the way they are billed: every chain of a session, each request at its own model's
price. ETU changes meaning, so the numbers of 0.1.0 (status line, `report`, `waste`, `gain`, snapshots) do not
compare with those of 0.2.0. `bench` results still do: bench keeps `weights`.

### Measuring
- Subagent transcripts (`<session>/subagents/*.jsonl`) are read too. Costs count every chain, re-reads are
  counted within each chain, and `report` and the status line include the subagents.
- Each request is priced at its own model's list price (5-minute and 1-hour cache writes, fast mode, US-only
  inference). ETU is now one input token of `etu_model` (Claude Opus 5.5), so it adds up across models and is
  proportional to $. `report` shows $ and the cost of each model; `waste`, `gain` and the context reminder
  show $ too. Prices live in the config (`prices`), models without a price use `weights`.
- `waste` (8): subagents by type and model. For agents that edited no file it estimates what a cheaper model
  (`waste_subagent_model`, Sonnet 5.5) would have cost.
- `waste` (9): file reads and searches (Read, Grep, cat, sed, grep…) in sessions and in subagents. A Bash
  command is named after any leading `cd <dir>` or `NAME=value` statement.
- `context` measures the fixed overhead of every request from the transcripts (Claude Code 2.1.30 or later):
  tool definitions, skill listing, instructions, system prompt, MCP servers, agent types. It prices each
  part, names the biggest tool definitions, and shows what plugins, MCP servers, skills and agent types that
  were never used cost.

## 0.1.0 (not tagged)

First version. Python ≥ 3.9, standard library only.

The tool was briefly published as `tokopt`. Everything is now named `toknix`; `toknix install` takes over
`tokopt` hooks, statusLine and rules blocks, and `toknix uninstall` removes them.

### Cutting waste
- `toknix run`: filtered output for tests, builds, installs, git and listings.
  - Error lines are always kept, word for word.
  - Output is held within a line budget and a token budget.
  - Very long lines are cut; hashes and timestamps are shortened in build and install logs.
  - The exit code is kept.
  - `toknix recall` gives the original output back, byte for byte.
- Claude Code hooks:
  - Bash: noisy commands go through `toknix run`.
  - Read guard: junk files, whole reads of big files and repeated reads are stopped, each with a way to
    read instead; content Greps get a `head_limit`.
  - Session start: a compaction resets the read counter.
- `toknix outline`, `symbol`, `refs`, `map`: code by name. Python through `ast`; JS/TS, Go, Java, Rust, PHP,
  Ruby, Dart, Kotlin and C# through regex, with strings and comments masked.
- Rules block v1 (282 tokens) for CLAUDE.md, AGENTS.md and GEMINI.md, plus a shorter variant.

### Measuring
- `toknix gain`: direct savings, the re-read estimate, recall rates per filter, top commands.
- `toknix report`, `waste` (7 groups with fixes), `context`, `snapshot`, from Claude Code and Codex transcripts.
- Char/token calibration from transcripts, used only when it is good enough.
- `toknix statusline`: context use, session ETU and savings, read incrementally.
- `toknix ui`: a read-only page on 127.0.0.1 with live activity, the original and filtered output of each
  cut run side by side, read-guard denials and savings per day. `toknix run` now also keeps the filtered
  output next to the raw one (`tee/<id>.out.log`).
- `toknix bench` / `compare`: A/B runs in throwaway git worktrees, verdict on ETU and on output tokens.

### Setting up
- `install.sh` / `install.ps1` (uv, or a private venv), `toknix install` / `uninstall` (byte-exact), `doctor`,
  `init-project`.
- CI on Linux, macOS and Windows × Python 3.9 and 3.13.

### Still to confirm on a real Claude Code session
See [docs/VERIFY.md](docs/VERIFY.md): `updatedInput` without a permission decision, permission rules on
rewritten commands, Claude Code's own Read de-duplication, how a Grep cut by `head_limit` is reported, and
the `init-project` deny rules.
