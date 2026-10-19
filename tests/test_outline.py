"""P3-T1: Python `extract` and `tokopt outline` (A5.1, A5.2)."""
from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import support

from tokopt import cli, hooks, outline
from tokopt.textutil import est_tokens

CODE = support.FIXTURES / "code"


def as_dicts(symbols) -> list[dict]:
    return [s._asdict() for s in symbols]


class TempDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, {"TOKOPT_HOME": str(self.dir / ".tokopt")})  # default token ratios
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name: str, data, mode: str = "w") -> Path:
        p = self.dir / name
        if isinstance(data, bytes):
            p.write_bytes(data)
        else:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(data)
        return p

    def outline(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["outline", *args])
        return code, out.getvalue(), err.getvalue()


class TestExtractPython(TempDir):
    def test_matches_expected_outline(self):
        expected = json.loads((CODE / "expected_outline.json").read_text(encoding="utf-8"))["sample.py"]
        self.assertEqual(as_dicts(outline.extract(CODE / "sample.py")), expected)

    def test_signature_keeps_strings_and_drops_comments(self):
        p = self.write("m.py", "def f(a='#)', b=\")\",  # a comment: with ) and :\n      c=1,\n      ):\n    pass\n")
        self.assertEqual(outline.extract(p)[0].signature, "def f(a='#)', b=\")\", c=1)")

    def test_class_with_keyword_and_generic_bases(self):
        p = self.write("m.py", "class A(\n    Generic[T],\n    metaclass=Meta,\n):\n    x: int\n")
        self.assertEqual(outline.extract(p)[0].signature, "class A(Generic[T], metaclass=Meta)")

    def test_line_numbers_with_crlf_and_form_feed(self):
        p = self.write("m.py", "x = 1\r\n\x0c\r\ndef f():\r\n    return 1\r\n\r\nclass C:\r\n    pass\r\n")
        self.assertEqual([(s.name, s.start, s.end) for s in outline.extract(p)], [("f", 3, 4), ("C", 6, 7)])

    def test_encoding_cookie(self):
        p = self.write("m.py", "# -*- coding: latin-1 -*-\ndef caf\xe9(): pass\n".encode("latin-1"))
        self.assertEqual(outline.extract(p)[0].signature, "def café()")

    def test_utf8_bom(self):
        p = self.write("m.py", "\ufeffdef f(): pass\n".encode("utf-8"))
        self.assertEqual(outline.extract(p)[0].name, "f")

    def test_syntax_error_is_reported(self):
        p = self.write("m.py", "def f(:\n")
        with self.assertRaises(outline.CannotOutline) as e:
            outline.extract(p)
        self.assertIn("line 1", str(e.exception))

    def test_unsupported_language_returns_none(self):
        self.assertIsNone(outline.extract(self.write("m.rb", "def x\nend\n")))

    def test_pyi_is_python(self):
        self.assertEqual(outline.extract(self.write("m.pyi", "def f(x: int) -> int: ...\n"))[0].name, "f")


class TestOutlineCommand(TempDir):
    def test_sample(self):
        code, out, err = self.outline(str(CODE / "sample.py"))
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(len(lines), 20 + 1)
        self.assertEqual(lines[2], "L22-33  def retry(times: int = 3)")
        self.assertEqual(lines[3], "  L23-32  def decorator(fn)")
        self.assertEqual(lines[4], "    L24-31  def wrapper(*args, **kwargs)")
        self.assertEqual(lines[8], "L57-90  class Shop(Base, metaclass=type)")
        self.assertEqual(lines[17], "      L88-88  def label(self)")
        text = (CODE / "sample.py").read_text(encoding="utf-8")
        # the hint points at the largest top-level symbol (Shop, L57-90)
        self.assertEqual(lines[-1], f"[93 lines, ~{est_tokens(text)} tokens; Read with offset=57 limit=34]")

    def test_no_symbols(self):
        text = "A = 1\nB = 2\n"
        code, out, _ = self.outline(str(self.write("consts.py", text)))
        self.assertEqual((code, out), (0, f"no functions or classes\n[2 lines, ~{est_tokens(text)} tokens]\n"))

    def test_unsupported_language_prints_the_first_200_lines(self):
        p = self.write("big.rb", "".join(f"puts {i}\n" for i in range(250)))
        code, out, _ = self.outline(str(p))
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "[tokopt: no outline for .rb files yet; first 200 of 250 lines]")
        self.assertEqual(lines[1:], [f"puts {i}" for i in range(200)])

    def test_python_that_does_not_parse_prints_the_first_200_lines(self):
        p = self.write("broken.py", "def f(:\n    pass\n")
        code, out, _ = self.outline(str(p))
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^\[tokopt: cannot outline broken\.py \(.+ at line 1\); first 2 of 2 lines\]\n")
        self.assertTrue(out.endswith("def f(:\n    pass\n"))

    def test_errors(self):
        for path, msg in ((self.dir / "nope.py", "no such file"), (self.dir, "is a directory"),
                          (self.write("blob.bin", b"\x00\x01\x02binary"), "binary file")):
            with self.subTest(path=path):
                code, out, err = self.outline(str(path))
                self.assertEqual((code, out), (1, ""))
                self.assertIn(msg, err)

    def test_usage(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as e:
                cli.main(["outline"])
        self.assertEqual(e.exception.code, 2)


class TestReadGuardHint(TempDir):
    """R2 tells the agent to run `tokopt outline <path>`; that exact command must work."""

    def test_hint_command_runs(self):
        big = self.write("my module.py", "".join(f"def f{i}(x):\n    return x + {i}  # {'pad ' * 6}\n\n"
                                                 for i in range(700)))  # 2,100 lines, > 800 × 40 bytes
        reply = hooks.hook_read({"session_id": "", "tool_name": "Read", "tool_input": {"file_path": str(big)}})
        reason = reply["hookSpecificOutput"]["permissionDecisionReason"]
        command = reason.split("`")[1]
        self.assertTrue(command.startswith("tokopt outline "))
        env = dict(os.environ, PYTHONPATH=str(support.SRC))
        p = subprocess.run([sys.executable, "-m", "tokopt", *shlex.split(command)[1:]], capture_output=True,
                           env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout.decode("utf-8").splitlines()
        self.assertEqual(out[0], "L1-2  def f0(x)")
        self.assertEqual(len(out), 701)


if __name__ == "__main__":
    unittest.main()
