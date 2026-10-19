"""P3-T2: regex `extract` for JS/TS, Go, Java, Rust, PHP, Ruby, Dart, Kotlin, C# (A5.1).

Each sample in tests/fixtures/code has a hand-written expectation in expected_outline.json.
A symbol matches when name, parent and start agree and end is off by at most one line.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support

from tokopt import cli, outline

CODE = support.FIXTURES / "code"
SAMPLES = ["sample.js", "sample.ts", "sample.go", "sample.java", "sample.rs", "sample.php", "sample.rb",
           "sample.dart", "sample.kt", "sample.cs"]
MIN_RECALL, MIN_PRECISION = 0.90, 0.85


def score(name: str) -> tuple[float, float, list, list]:
    expected = json.loads((CODE / "expected_outline.json").read_text(encoding="utf-8"))[name]
    got = outline.extract(CODE / name)
    unmatched = list(got)
    missed = []
    for e in expected:
        hit = next((g for g in unmatched if (g.name, g.parent, g.start) == (e["name"], e["parent"], e["start"])
                    and abs(g.end - e["end"]) <= 1), None)
        if hit is None:
            missed.append(e)
        else:
            unmatched.remove(hit)
    matched = len(expected) - len(missed)
    return matched / len(expected), (matched / len(got) if got else 0.0), missed, unmatched


class TestSamples(unittest.TestCase):
    def test_sample_requirements(self):  # plan: ≥ 80 lines with a class, a method, a nested function, "{" in a string
        for name in SAMPLES:
            with self.subTest(sample=name):
                text = (CODE / name).read_text(encoding="utf-8")
                self.assertGreaterEqual(len(text.splitlines()), 80)
                self.assertRegex(text, r"""["'`][^"'`\n]*\{""")

    def test_recall_and_precision(self):
        for name in SAMPLES:
            with self.subTest(sample=name):
                recall, precision, missed, extra = score(name)
                detail = (f"\nmissed: {[(m['name'], m['start'], m['end'], m['parent']) for m in missed]}"
                          f"\nextra:  {[(x.name, x.start, x.end, x.parent) for x in extra]}")
                self.assertGreaterEqual(recall, MIN_RECALL, f"recall {recall:.0%}{detail}")
                self.assertGreaterEqual(precision, MIN_PRECISION, f"precision {precision:.0%}{detail}")


class TestMasking(unittest.TestCase):
    """Strings and comments never open or close a block."""

    def ends(self, ext: str, text: str) -> list[tuple[str, int, int]]:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / f"m{ext}"
            p.write_text(text, encoding="utf-8")
            return [(s.name, s.start, s.end) for s in outline.extract(p)]

    def test_braces_in_strings_and_comments(self):
        self.assertEqual(self.ends(".js", 'function a() {\n  const s = "}";  // }\n  /* } */\n  return `}${"}"}`;\n}\n'
                                          "function b() {}\n"), [("a", 1, 5), ("b", 6, 6)])

    def test_regex_literal_with_brace(self):
        self.assertEqual(self.ends(".js", "function a(s) {\n  return /\\}/.test(s);\n}\n"), [("a", 1, 3)])

    def test_rust_lifetimes_are_not_strings(self):
        self.assertEqual(self.ends(".rs", "fn a<'x>(s: &'x str) -> &'x str {\n    s\n}\nfn b() { let c = '}'; }\n"),
                         [("a", 1, 3), ("b", 4, 4)])

    def test_go_raw_string(self):
        self.assertEqual(self.ends(".go", "func a() string {\n\treturn `}\n{`\n}\n"), [("a", 1, 4)])

    def test_csharp_verbatim_and_interpolated(self):
        text = ('class A\n{\n    string P = @"C:\\x ""}"" ";\n    string Q() => $"{{ {P} }}";\n}\n')
        self.assertEqual(self.ends(".cs", text), [("A", 1, 5), ("Q", 4, 4)])

    def test_kotlin_and_dart_interpolation_with_quotes(self):
        self.assertEqual(self.ends(".kt", 'fun a(): String {\n    return "${"}"}"\n}\n'), [("a", 1, 3)])
        self.assertEqual(self.ends(".dart", "String a() {\n  return '${'}'}';\n}\n"), [("a", 1, 3)])

    def test_ruby_heredoc_and_strings(self):
        text = 'def a\n  x = "end"\n  <<~SQL\n    end\n  SQL\nend\ndef b; end\n'
        self.assertEqual(self.ends(".rb", text), [("a", 1, 6), ("b", 7, 7)])

    def test_jsx_apostrophe(self):
        self.assertEqual(self.ends(".jsx", "function A() {\n  return <p>Don't {x}</p>;\n}\nfunction B() {}\n"),
                         [("A", 1, 3), ("B", 4, 4)])

    def test_void_methods(self):  # `void` once sat in the keyword list and hid every void method
        self.assertEqual(self.ends(".java", "class A {\n    public void run() {\n    }\n}\n"), [("A", 1, 4), ("run", 2, 3)])
        self.assertEqual(self.ends(".cs", "class A\n{\n    void Run() { }\n}\n"), [("A", 1, 4), ("Run", 3, 3)])
        self.assertEqual(self.ends(".dart", "void main() {\n}\n"), [("main", 1, 2)])

    def test_go_short_assignment_closure_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.go"
            p.write_text("func a() {\n\tf := func(x int) int {\n\t\treturn x\n\t}\n}\n")
            self.assertEqual(outline.extract(p)[1].signature, "f := func(x int) int")

    def test_js_arrow_signature_keeps_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.ts"
            p.write_text("export const add = async (a: number, b: number): Promise<number> => {\n  return a + b;\n};\n")
            self.assertEqual(outline.extract(p)[0].signature,
                             "export const add = async (a: number, b: number): Promise<number> =>")

    def test_very_long_lines_stay_fast(self):
        # `    "<57k chars>",` is blanks plus a comma once masked; regex backtracking once took minutes on it.
        import time
        long = "abc " * 15000
        cases = {".dart": f'const m = [\n    "{long}",\n];\nint f() {{\n}}\n',
                 ".ts": f'const m = [\n    "{long}",\n];\nfunction f() {{\n}}\n',
                 ".js": f'const m = [\n    "{long}",\n];\nfunction f() {{\n}}\n',
                 ".kt": f'val m = listOf(\n    "{long}",\n)\nfun f() {{\n}}\n',
                 ".java": f'class A {{\n  String[] m = {{\n    "{long}",\n  }};\n  int f() {{\n  }}\n}}\n',
                 ".cs": f'class A\n{{\n  string[] m = {{\n    "{long}",\n  }};\n  int F() {{ }}\n}}\n',
                 ".go": f'var m = []string{{\n\t"{long}",\n}}\nfunc f() {{\n}}\n',
                 ".rs": f'const M: [&str; 1] = [\n    "{long}",\n];\nfn f() {{\n}}\n',
                 ".rb": f'M = [\n  "{long}",\n]\ndef f\nend\n',
                 ".php": f'<?php\n$m = [\n    "{long}",\n];\nfunction f() {{\n}}\n'}
        for ext, text in cases.items():
            with self.subTest(ext=ext):
                t = time.perf_counter()
                names = [n for n, _, _ in self.ends(ext, text)]
                self.assertLess(time.perf_counter() - t, 1.0)
                self.assertIn(names[-1], ("f", "F"))

    def test_jsx_closing_tags_are_not_regex(self):  # found in real .tsx files
        text = ("function A() {\n  return <td>{x ? <b>{y}</b> : z}</td>;\n}\n"
                "function B() {\n  return <p>/path {q}</p>;\n}\nfunction C() {}\n")
        self.assertEqual(self.ends(".tsx", text), [("A", 1, 3), ("B", 4, 6), ("C", 7, 7)])
        self.assertEqual(self.ends(".tsx", "function D() {\n  return <div>{a ? <X k={b} /> : null}</div>;\n}\n"
                                           "function E() {}\n"), [("D", 1, 3), ("E", 4, 4)])

    def test_dollar_brace_in_plain_js_string_is_text(self):  # highlight.js: illegal: '[${=\\n]'
        self.assertEqual(self.ends(".js", "function a() {\n  return '[${=]';\n}\nfunction b() {}\n"),
                         [("a", 1, 3), ("b", 4, 4)])

    def test_regex_with_quote_inside_template_interpolation(self):  # eslintrc config-validator.js
        text = 'function a(s) {\n  throw new Error(`x ${s.replace(/\'/g, "\\"")} {`);\n}\nfunction b() {}\n'
        self.assertEqual(self.ends(".js", text), [("a", 1, 3), ("b", 4, 4)])

    def test_nested_block_comments(self):  # Rust, Kotlin and Dart nest /* */; found in a real crate
        for ext, fn in ((".rs", "fn"), (".kt", "fun"), (".dart", "void")):
            with self.subTest(ext=ext):
                # without nesting, "} */" after the inner */ would close a() on line 2
                text = f"{fn} a() {{\n    /* x /* y */ }} */\n    z();\n}}\n{fn} b() {{\n}}\n"
                self.assertEqual(self.ends(ext, text), [("a", 1, 4), ("b", 5, 6)])
        # Java does not nest: the comment ends at the first */ and `void f()` is code
        self.assertEqual(self.ends(".java", "class A {\n  /* /* */ void f() {\n  }\n}\n"), [("A", 1, 4), ("f", 2, 3)])

    def test_rust_strings_span_lines(self):  # prettyplease / winnow tests: "{\n ...\n}"
        # the brace is on a continuation line of the string, not on the line with the quote
        self.assertEqual(self.ends(".rs", 'fn a() {\n    let s = "\n}\n";\n}\nfn b() {}\n'), [("a", 1, 5), ("b", 6, 6)])

    def test_php_hash_comment_and_attribute(self):
        text = "<?php\n# }\n#[Attr]\nfunction a() {\n  return '}';\n}\n"
        self.assertEqual(self.ends(".php", text), [("a", 3, 6)])


class TestOutlineOtherLanguages(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"TOKOPT_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def outline(self, path) -> list[str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["outline", str(path)]), 0)
        return out.getvalue().splitlines()

    def test_go(self):
        lines = self.outline(CODE / "sample.go")
        self.assertIn("L43-56  func (c Cart) Total(store Store) (Money, error)", lines)
        self.assertIn("  L59-61  format := func(sku string, qty int) string", lines)
        self.assertTrue(lines[-1].startswith("[95 lines, ~"))

    def test_multiline_signature_is_tidied(self):  # like Python's: no blank inside brackets, no trailing comma
        lines = self.outline(CODE / "sample.ts")
        self.assertIn("L33-42  export async function loadAll(urls: string[], retries = 2): Promise<Map<string, Item[]>>",
                      lines)

    def test_signature_is_the_header_without_body(self):
        lines = self.outline(CODE / "sample.java")
        self.assertIn("  L23-29  public synchronized void add(String sku, int qty) throws IllegalArgumentException",
                      lines)
        self.assertIn("L8-85  public class Shop implements Comparable<Shop>", lines)  # annotation counted in start

    def test_unknown_extension_still_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.lua"
            p.write_text("function f() end\n")
            self.assertEqual(self.outline(p)[0], "[tokopt: no outline for .lua files yet; first 1 of 1 lines]")


if __name__ == "__main__":
    unittest.main()
