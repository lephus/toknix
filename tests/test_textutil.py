"""P1-T2: normalize (A2.2) and est_tokens by character class (A1, N1.2a)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from toknix import config
from toknix.textutil import char_classes, est_tokens, normalize, resolve_cr, strip_ansi


class TestNormalize(unittest.TestCase):
    def test_strips_csi_colors(self):
        self.assertEqual(strip_ansi("\x1b[31;1mred\x1b[0m ok"), "red ok")

    def test_strips_osc_hyperlink_bel_and_st(self):
        self.assertEqual(strip_ansi("\x1b]8;;http://x\x07link\x1b]8;;\x07"), "link")
        self.assertEqual(strip_ansi("\x1b]0;title\x1b\\text"), "text")

    def test_strips_keypad_mode(self):
        self.assertEqual(strip_ansi("\x1b=a\x1b>b"), "ab")

    def test_crlf_becomes_lf(self):
        self.assertEqual(normalize("a\r\nb\r\nc"), ["a", "b", "c"])

    def test_progress_bar_keeps_last_frame(self):
        self.assertEqual(normalize("10%\r50%\r100%"), ["100%"])

    def test_progress_bar_ignores_blank_final_frame(self):
        self.assertEqual(resolve_cr("done\r   "), "done")
        self.assertEqual(resolve_cr("\r\r"), "")

    def test_progress_with_ansi_and_crlf(self):
        text = "start\r\n\x1b[2K 50%\r\x1b[2K 100%\x1b[0m\r\nend"
        self.assertEqual(normalize(text), ["start", " 100%", "end"])

    def test_rstrips_each_line(self):
        self.assertEqual(normalize("a  \n  b\t"), ["a", "  b"])


class TestEstTokens(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(est_tokens(""), 0)

    def test_ascii(self):
        self.assertEqual(est_tokens("hello world"), 3)          # ceil(11 / 4)
        self.assertEqual(est_tokens("x" * 400), 100)

    def test_long_plain_word_is_not_hex(self):
        self.assertEqual(est_tokens("internationalization"), 5)  # 20 chars / 4
        self.assertEqual(est_tokens("usr/lib/python3/site"), 5)

    def test_hex_run(self):
        sha = "0123456789abcdef" * 2 + "01234567"                 # 40 hex chars
        self.assertEqual(est_tokens(sha), 20)                     # 40 / 2
        self.assertEqual(est_tokens("commit " + sha), 2 + 20)     # ceil(7 / 4) + 20

    def test_short_hex_is_default(self):
        self.assertEqual(est_tokens("0123456789abcde"), 4)        # 15 chars → default class

    def test_base64_run(self):
        b64 = "SGVsbG8gV29ybGQhIEhlbGxvIQ=="                    # 28 chars incl. padding
        self.assertEqual(est_tokens(b64), 14)

    def test_vietnamese(self):
        text = "tiếng việt"                             # "tiếng việt": 2 non-ASCII, 8 ASCII
        self.assertEqual(est_tokens(text), 2 + 2)                 # ceil(2 / 1.5) + ceil(8 / 4)

    def test_mixed(self):
        sha = "deadbeef" * 4                                      # 32 hex
        text = "━" * 3 + " id=" + sha + " ✓"           # "━━━ id=<sha> ✓"
        # non_ascii 4 → ceil(4/1.5)=3; hex 32 → 16; default " id=" + " " = 5 → 2
        self.assertEqual(est_tokens(text), 3 + 16 + 2)

    def test_ratios_argument(self):
        ratios = {"default": 2.0, "hex": 1.0, "non_ascii": 1.0}
        self.assertEqual(est_tokens("abcdefgh", ratios), 4)
        self.assertEqual(est_tokens("ệệ", ratios), 2)

    def test_missing_ratio_falls_back_to_default(self):
        self.assertEqual(est_tokens("abcdefgh", {"default": 8.0}), 1)
        self.assertEqual(est_tokens("ệệệ", {"default": 8.0}), 2)  # non_ascii 1.5

    def test_config_defaults(self):
        self.assertEqual(config.DEFAULTS["token_ratios"],
                         {"default": 4.0, "hex": 2.0, "non_ascii": 1.5})

    def test_ratios_read_from_user_config(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"token_ratios": {"default": 2.0, "hex": 2.0, "non_ascii": 1.5}}, f)
            with mock.patch.dict(os.environ, {"TOKNIX_HOME": d}):
                cfg = config.load()
        self.assertEqual(est_tokens("abcdefgh", cfg["token_ratios"]), 4)


class TestCharClasses(unittest.TestCase):
    """Chars per class: what `runs` stores so reports can apply any ratio later (P1-T5, A6.4)."""

    def test_counts_each_class(self):
        sha = "0123456789abcdef" * 2
        self.assertEqual(char_classes(f"commit {sha} ✓ đã"),
                         {"default": len("commit ") + 2, "hex": 32, "non_ascii": 3})

    def test_empty(self):
        self.assertEqual(char_classes(""), {"default": 0, "hex": 0, "non_ascii": 0})


if __name__ == "__main__":
    unittest.main()
