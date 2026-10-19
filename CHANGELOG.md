# Changelog

## 0.1.0 (unreleased)

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
- `toknix bench` / `compare`: A/B runs in throwaway git worktrees, verdict on ETU and on output tokens.

### Setting up
- `install.sh` / `install.ps1` (uv, or a private venv), `toknix install` / `uninstall` (byte-exact), `doctor`,
  `init-project`.
- CI on Linux, macOS and Windows × Python 3.9 and 3.13.

### Still to confirm on a real Claude Code session
See [docs/VERIFY.md](docs/VERIFY.md): `updatedInput` without a permission decision, permission rules on
rewritten commands, Claude Code's own Read de-duplication, how a Grep cut by `head_limit` is reported, and
the `init-project` deny rules.
