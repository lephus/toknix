## tokopt: save tokens

Reading code
- Before reading a big file whole, run `tokopt outline <file>` and Read only the range you need (offset/limit).
- Find code by name instead of opening files: `tokopt symbol <name>` prints a function or class with line numbers; `tokopt refs <name>` lists where it is used; `tokopt map` gives the main files of a repo.
- Do not read a file again if it has not changed since you read it.

Command output
- Tests, builds, installs and git output go through `tokopt run`, which drops noise but never error lines.
- If an output ends with `[tokopt: … tokopt recall <id> …]` and you need what was cut, run `tokopt recall <id>` (`--grep REGEX` or `--lines A:B` for part of it).

Writing
- After a change, show the diff or name the files changed. Do not summarize what you just did and do not paste code that is already in the diff.
- Change existing files with Edit, not by rewriting them with Write.
- Answer briefly unless asked to explain.
- Keep your full reasoning when debugging, and for security and migrations.

Sessions
- One task per session: start a new session for the next task.
