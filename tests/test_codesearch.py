"""P3-T3: `toknix symbol` and `toknix refs` (A5.2)."""
from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support

from toknix import cli, codesearch

REF_LINE = re.compile(r"^\s+(\d+): ")


def git(root: Path, *args: str):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                            GIT_COMMITTER_EMAIL="t@t"))


class Repo(unittest.TestCase):
    """A temp git repo; commands run with it as the working directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        patcher = mock.patch.dict(os.environ, {"TOKNIX_HOME": str(self.root / ".toknix-home")})
        patcher.start()
        self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)

    def write(self, rel: str, text, git_add: bool = True) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8")
        return p

    def init_git(self):
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "x")

    def toknix(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TestSourceFiles(Repo):
    def make(self):
        self.write("src/app.py", "def main():\n    pass\n")
        self.write("src/ui.ts", "export function a() {}\n")
        self.write("node_modules/x/index.js", "function junk() {}\n")
        self.write("dist/bundle.js", "function junk() {}\n")
        self.write("big.py", "x = 1\n" * 200_000)  # > 1 MB
        self.write("blob.py", b"\x00\x01binary")
        self.write("README.md", "# docs\n")
        self.write(".gitignore", "ignored.py\n")
        self.write("ignored.py", "def hidden(): pass\n")

    def rel(self, files) -> list[str]:
        return [p.relative_to(self.root).as_posix() for p in files]

    def test_git_repo(self):
        self.make()
        self.init_git()
        self.write("src/new_untracked.py", "def fresh(): pass\n")  # not committed, not ignored: included
        files = self.rel(codesearch.source_files(self.root))
        self.assertEqual(files, ["src/app.py", "src/new_untracked.py", "src/ui.ts"])

    def test_plain_directory(self):
        self.make()
        files = self.rel(codesearch.source_files(self.root))
        self.assertEqual(files, ["ignored.py", "src/app.py", "src/ui.ts"])  # no .gitignore without git


class TestSymbol(Repo):
    def test_budget_cut_in_the_toknix_repo(self):  # plan criterion
        os.chdir(support.ROOT)
        code, out, err = self.toknix("symbol", "budget_cut")
        self.assertEqual((code, err), (0, ""))
        src = (support.ROOT / "src/toknix/filters.py").read_text(encoding="utf-8")
        node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "budget_cut")
        lines = src.split("\n")
        expected = [f"{i:>5}  {lines[i - 1]}".rstrip() for i in range(node.lineno, node.end_lineno + 1)]
        out_lines = out.splitlines()
        self.assertEqual(out_lines[0], f"src/toknix/filters.py:{node.lineno}-{node.end_lineno}  function budget_cut")
        self.assertEqual(out_lines[1:1 + len(expected)], expected)
        self.assertEqual(out_lines[-1], "[1 match]")

    def test_class_dot_method_and_kind(self):
        self.write("shop.py", "class Cart:\n    def add(self, x):\n        return x\n\n\ndef add(a, b):\n    return a + b\n")
        self.write("web/cart.ts", "export class Cart {\n  add(x: number) {\n    return x;\n  }\n}\n")
        self.init_git()
        code, out, _ = self.toknix("symbol", "Cart.add")
        self.assertEqual(code, 0)
        self.assertIn("shop.py:2-3  method Cart.add", out)
        self.assertIn("web/cart.ts:2-4  method Cart.add", out)
        self.assertNotIn("function add", out)
        self.assertIn("[2 matches]", out)
        code, out, _ = self.toknix("symbol", "add", "--kind", "function")
        self.assertIn("shop.py:6-7  function add", out)
        self.assertIn("[1 match]", out)
        code, out, _ = self.toknix("symbol", "Cart", "--kind", "class")
        self.assertIn("[2 matches]", out)

    def test_more_than_three_matches(self):
        for i in range(5):
            self.write(f"m{i}.py", f"def run():\n    return {i}\n")
        self.init_git()
        code, out, _ = self.toknix("symbol", "run")
        self.assertEqual(out.count("    2      return"), 3)
        self.assertIn("also: m3.py:1-2  function run", out)
        self.assertIn("also: m4.py:1-2  function run", out)
        self.assertIn("[5 matches; code shown for the first 3]", out)

    def test_long_symbol_is_capped_at_300_lines(self):
        self.write("long.py", "def huge():\n" + "".join(f"    x{i} = {i}\n" for i in range(400)))
        self.init_git()
        out = self.toknix("symbol", "huge")[1].splitlines()
        self.assertEqual(out[0], "long.py:1-401  function huge")
        self.assertEqual(out[300], "  300      x298 = 298")
        self.assertEqual(out[301], "  … 101 more lines; Read long.py with offset=301 limit=101")

    def test_path_option(self):
        self.write("a/x.py", "def f(): pass\n")
        self.write("b/x.py", "def f(): pass\n")
        self.init_git()
        code, out, _ = self.toknix("symbol", "f", "--path", "b")
        self.assertIn("b/x.py:1-1  function f", out)
        self.assertNotIn("a/x.py", out)

    def test_not_found(self):
        self.write("x.py", "def g(): pass\n")
        self.init_git()
        code, out, err = self.toknix("symbol", "nope")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no symbol named 'nope' in 1 source files", err)


class TestRefs(Repo):
    def test_classify_in_the_toknix_repo(self):  # plan criterion: every use, no definition line
        os.chdir(support.ROOT)
        code, out, err = self.toknix("refs", "classify")
        self.assertEqual((code, err), (0, ""))
        got, current = set(), None
        for line in out.splitlines():
            m = REF_LINE.match(line)
            if m:
                got.add((current, int(m.group(1))))
            elif not line.startswith("["):
                current = line.strip()
        expected = set()
        for rel in subprocess.run(["git", "-C", str(support.ROOT), "ls-files", "*.py"], capture_output=True,
                                  text=True).stdout.split():
            if rel.startswith("tests/fixtures/"):
                continue
            src = (support.ROOT / rel).read_text(encoding="utf-8")
            defs = {n.lineno for n in ast.walk(ast.parse(src))
                    if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == "classify"}
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):  # independent oracle: NAME tokens
                if tok.type == tokenize.NAME and tok.string == "classify" and tok.start[0] not in defs:
                    expected.add((rel, tok.start[0]))
        self.assertTrue(expected)
        self.assertEqual(got, expected)
        self.assertRegex(out.splitlines()[-1], r"^\[\d+ references in \d+ files; definition not listed: "
                                               r"src/toknix/filters\.py:\d+\]$")

    def test_strings_comments_and_word_boundaries(self):
        self.write("a.py", 'def total(x):\n    return x\n\n# total in a comment\ns = "total in a string"\n'
                           "total_all = 1\nprint(total(3))\nf = f'{total(4)}'\n")
        self.write("b.ts", "// total\nconst t = `total ${total(1)}`;\nexport const subtotal = 1;\n")
        self.init_git()
        code, out, _ = self.toknix("refs", "total")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[:2], ["a.py", "     7: print(total(3))"])
        self.assertIn("b.ts", lines)
        self.assertIn("     2: const t = `total ${total(1)}`;", lines)
        self.assertNotIn("total_all", out)
        self.assertNotIn("subtotal", out)
        self.assertNotIn("comment", out)

    def test_more_than_fifty_lines_are_summarised_not_dropped(self):
        self.write("lib.py", "def ping():\n    pass\n")
        self.write("use1.py", "".join("ping()\n" for _ in range(40)))
        self.write("use2.py", "".join("ping()\n" for _ in range(30)))
        self.init_git()
        out = self.toknix("refs", "ping")[1].splitlines()
        shown = [l for l in out if REF_LINE.match(l)]
        self.assertEqual(len(shown), 50)
        self.assertIn("use2.py: 20 more at lines 11-30", out)
        self.assertEqual(out[-1], "[70 references in 2 files, 50 shown; definition not listed: lib.py:1]")

    def test_class_dot_name_searches_the_last_part(self):
        self.write("a.py", "class Cart:\n    def add(self):\n        pass\n\nCart().add()\n")
        self.init_git()
        out = self.toknix("refs", "Cart.add")[1]
        self.assertIn("     5: Cart().add()", out)

    def test_none(self):
        self.write("a.py", "x = 1\n")
        self.init_git()
        code, out, err = self.toknix("refs", "missing")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no references to 'missing'", err)


class TestPrefilter(unittest.TestCase):
    """The cheap per-file checks may skip a file only when it really has no match."""

    def files(self):
        return codesearch.source_files(support.ROOT) + sorted((support.FIXTURES / "code").glob("sample.*"))

    def test_symbol_prefilter_never_drops_a_match(self):
        files = self.files()
        cache = {p: codesearch.outline.extract(p) for p in files}  # both paths see the same parse; only the
        names = {s.name for symbols in cache.values() for s in (symbols or [])}  # prefilter differs
        self.assertGreater(len(names), 300)
        with mock.patch.object(codesearch.outline, "extract", side_effect=lambda p: cache[p]):
            for name in sorted(names):
                with self.subTest(name=name):
                    fast = codesearch.find_symbols(name, files)
                    with mock.patch.object(codesearch, "might_declare", return_value=True):
                        slow = codesearch.find_symbols(name, files)
                    self.assertEqual(fast, slow)

    def test_refs_prefilter_never_drops_a_reference(self):
        files = self.files()
        for name in ("classify", "cn", "add", "Cart", "run", "load", "line", "x", "total", "self"):
            with self.subTest(name=name):
                fast = codesearch.find_refs(name, files)
                with mock.patch.object(codesearch, "mentions_word", return_value=True):
                    slow = codesearch.find_refs(name, files)
                self.assertEqual(fast, slow)


class TestSpeed(unittest.TestCase):
    """Plan: a 2,000-file repo answers in < 2 s (scripts/bench_codesearch.py)."""

    def test_two_thousand_files(self):
        sys.path.insert(0, str(support.ROOT / "scripts"))
        try:
            import bench_codesearch
        finally:
            sys.path.pop(0)
        timings = bench_codesearch.run(2000)
        limit = 6.0 if os.environ.get("CI") else 2.0  # shared CI runners are slower; the plan's bar is local
        for command, seconds in timings.items():
            with self.subTest(command=command):
                self.assertLess(seconds, limit)


if __name__ == "__main__":
    unittest.main()
