"""Command-line entry point. Each v0.1 command is registered here; later tasks replace the stubs."""
from __future__ import annotations

import argparse
import shlex
import sys

from tokopt import __version__

COMMANDS = [
    ("run", "run a command and print filtered output (A2)"),
    ("recall", "print the full output of a filtered run (A2.7)"),
    ("gain", "tokens saved by filters and guards (A6.2)"),
    ("hook", "Claude Code hook entry point: bash | read | session-start (A3, A4)"),
    ("outline", "symbols of a file with line ranges (A5)"),
    ("symbol", "print the code of a symbol (A5)"),
    ("refs", "list references to a name (A5)"),
    ("map", "ranked repo map within a token budget (A5)"),
    ("report", "token usage from agent transcripts (A6.2)"),
    ("waste", "where tokens are wasted, with fixes (A6.2)"),
    ("context", "instruction files, MCP servers and skills loaded each session (A6.2)"),
    ("snapshot", "save a report summary under a label (A6.2)"),
    ("bench", "run A/B tasks with a real agent (A6.3)"),
    ("compare", "compare two bench labels (A6.3)"),
    ("statusline", "Claude Code status line (A7)"),
    ("install", "install hooks and rules (A8)"),
    ("uninstall", "remove hooks and rules (A8)"),
    ("init-project", "add project-level deny rules (A8)"),
    ("doctor", "check the installation (A8)"),
]


def positive_int(text: str) -> int:
    n = int(text)
    if n < 1:
        raise ValueError(text)
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokopt", description="Cut the tokens AI coding agents waste, and prove it.")
    parser.add_argument("--version", action="version", version=f"tokopt {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, help_text in COMMANDS:
        p = sub.add_parser(name, help=help_text)
        if name == "run":
            p.add_argument("cmd", nargs=argparse.REMAINDER, metavar="-- <command>",
                           help='one quoted string (as hook bash writes it), or the words of the command')
        elif name == "outline":
            p.add_argument("file")
        elif name == "gain":
            p.add_argument("--json", action="store_true", help="machine-readable output")
            p.add_argument("--days", type=positive_int, metavar="N", help="only the last N days (default: all)")
        elif name == "recall":
            p.add_argument("id")
            p.add_argument("--grep", metavar="REGEX", help="only lines matching REGEX, with line numbers")
            p.add_argument("--lines", metavar="A:B", help="only lines A to B (1-based, inclusive)")
        else:
            p.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def _setup(command: str) -> int:
    from tokopt import installer
    from tokopt.runner import write_out
    if command == "doctor":
        lines, ok = installer.doctor()
        write_out("\n".join(lines) + "\n")
        return 0 if ok else 1
    try:
        msgs = installer.install() if command == "install" else installer.uninstall()
    except (installer.InstallError, OSError) as e:
        print(f"tokopt {command}: {e}", file=sys.stderr)
        return 1
    if command == "install":
        msgs.append("restart Claude Code to load the hooks; check with `tokopt doctor`")
    write_out("".join(m + "\n" for m in msgs))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
    if ns.command == "hook":
        try:
            from tokopt import hooks
            return hooks.main(ns.args[0] if ns.args else "")
        except Exception:  # a hook must never break the agent, not even a broken install
            return 0
    if ns.command == "run":
        words = ns.cmd[1:] if ns.cmd[:1] == ["--"] else ns.cmd
        if not words:
            print('usage: tokopt run -- "<command>"', file=sys.stderr)
            return 2
        from tokopt import runner
        return runner.run(words[0] if len(words) == 1 else shlex.join(words))
    if ns.command == "outline":
        from tokopt import outline
        return outline.run(ns.file)
    if ns.command == "gain":
        from tokopt import measure
        return measure.gain(ns.days, ns.json)
    if ns.command == "statusline":  # stub until P4-T4: `install` adds it, so it must show nothing, not an error
        try:
            if not sys.stdin.isatty():
                sys.stdin.read()  # Claude Code writes the session JSON; take it so the writer never sees EPIPE
        except (OSError, ValueError):
            pass
        return 0
    if ns.command in ("install", "uninstall", "doctor"):
        return _setup(ns.command)
    if ns.command == "recall":
        from tokopt import runner
        return runner.recall(ns.id, ns.grep, ns.lines)
    print(f"tokopt {ns.command}: not implemented yet", file=sys.stderr)
    return 2
