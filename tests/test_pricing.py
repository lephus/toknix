"""P8-T2: prices per model, ETU proportional to $ (A1)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from toknix import config, pricing
from toknix.transcripts import Request

OPUS_55 = [4, 5, 8, 0.2, 20]  # $/MTok: input, 5m write, 1h write, cache read, output (2026-10-21)


def cfg(**over) -> dict:
    return {**config.DEFAULTS, **over}


def req(model="claude-opus-5-5", input=0, cache_write=0, cache_write_1h=0, cache_read=0, output=0, speed="",
        geo="") -> Request:
    r = Request(("m", "r"), None, model, input, cache_write, cache_read, output, cache_write_1h)
    r.speed, r.geo = speed, geo
    return r


class TestLookup(unittest.TestCase):
    def setUp(self):
        self.p = pricing.Pricer(cfg())

    def test_list_prices(self):
        self.assertEqual(self.p.rates("claude-opus-5-5"), OPUS_55)
        self.assertEqual(self.p.rates("claude-fable-5-1"), [10, 12.5, 20, 0.25, 50])
        self.assertEqual(self.p.rates("claude-sonnet-5-5"), [2, 2.5, 4, 0.1, 10])
        self.assertEqual(self.p.rates("claude-haiku-4-5"), [1, 1.25, 2, 0.1, 5])

    def test_longest_key_wins(self):
        self.assertEqual(self.p.rates("claude-opus-5"), [5, 6.25, 10, 0.5, 25])
        self.assertEqual(self.p.rates("claude-opus-5-5"), OPUS_55)  # not claude-opus-5
        self.assertEqual(self.p.rates("claude-opus-4-1"), [15, 18.75, 30, 1.5, 75])
        self.assertEqual(self.p.rates("claude-opus-4-20250514"), [15, 18.75, 30, 1.5, 75])
        self.assertEqual(self.p.rates("claude-opus-4-6"), [5, 6.25, 10, 0.5, 25])

    def test_dated_platform_and_suffixed_ids(self):
        for model in ("claude-haiku-4-5-20251001", "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                      "claude-haiku-4-5@20251001"):
            with self.subTest(model=model):
                self.assertEqual(self.p.rates(model), [1, 1.25, 2, 0.1, 5])
        self.assertEqual(self.p.rates("claude-sonnet-4-6"), [3, 3.75, 6, 0.3, 15])
        self.assertEqual(self.p.rates("anthropic.claude-opus-5-5"), OPUS_55)

    def test_unknown_models_have_no_price(self):
        for model in ("", "gpt-5-codex", "<synthetic>", "claude-opusx", "claude-unknown-9"):
            with self.subTest(model=model):
                self.assertIsNone(self.p.rates(model))

    def test_haiku_55_prompt_over_100k(self):
        self.assertEqual(self.p.rates("claude-haiku-5-5", 100000), [0.1, 0.125, 0.2, 0.01, 0.5])
        self.assertEqual(self.p.rates("claude-haiku-5-5", 100001), [0.5, 0.625, 1, 0.05, 2.5])

    def test_fast_mode_and_us_inference(self):
        self.assertEqual(self.p.rates("claude-opus-5-5", speed="fast"), [x * 2 for x in OPUS_55])
        for got, want in zip(self.p.rates("claude-opus-5-5", speed="fast", geo="us"), OPUS_55):
            self.assertAlmostEqual(got, want * 2.2)
        self.assertEqual(self.p.rates("claude-opus-5-5", speed="standard", geo="global"), OPUS_55)


class TestWeights(unittest.TestCase):
    def test_etu_unit_is_an_input_token_of_etu_model(self):
        p = pricing.Pricer(cfg())
        self.assertEqual(p.weights("claude-opus-5-5"), [1, 1.25, 2, 0.05, 5])
        self.assertEqual(p.weights("claude-sonnet-5-5"), [0.5, 0.625, 1, 0.025, 2.5])
        self.assertEqual(p.ref, 4)

    def test_unpriced_models_use_config_weights(self):
        p = pricing.Pricer(cfg(weights=[1, 2, 0.5, 4]))
        self.assertEqual(p.weights("gpt-5-codex"), [1, 2, 2, 0.5, 4])

    def test_no_etu_model_means_config_weights_for_all(self):
        for model in ("", "nope"):
            with self.subTest(etu_model=model):
                p = pricing.Pricer(cfg(etu_model=model))
                self.assertEqual(p.weights("claude-opus-5-5"), [1, 1.25, 1.25, 0.1, 5])
                self.assertEqual(p.usd(req(output=1_000_000)), 20)  # $ only needs the price table

    def test_request_etu_and_usd(self):
        p = pricing.Pricer(cfg())
        r = req(input=1000, cache_write=3000, cache_write_1h=1000, cache_read=100000, output=2000)
        self.assertAlmostEqual(p.etu(r), 1000 + 2000 * 1.25 + 1000 * 2 + 100000 * 0.05 + 2000 * 5)
        self.assertAlmostEqual(p.usd(r), (1000 * 4 + 2000 * 5 + 1000 * 8 + 100000 * 0.2 + 2000 * 20) / 1e6)
        self.assertAlmostEqual(p.etu(r), p.usd(r) * 1e6 / 4)  # ETU is proportional to $
        self.assertAlmostEqual(p.output_etu(r), 2000 * 5)
        self.assertIsNone(p.usd(req(model="gpt-5", output=10)))

    def test_haiku_tier_follows_the_request_prompt(self):
        p = pricing.Pricer(cfg())
        small = req("claude-haiku-5-5", input=10, cache_read=99990, output=1000)
        big = req("claude-haiku-5-5", input=10, cache_read=99991, output=1000)
        self.assertAlmostEqual(p.usd(small), (10 * 0.1 + 99990 * 0.01 + 1000 * 0.5) / 1e6)
        self.assertAlmostEqual(p.usd(big), (10 * 0.5 + 99991 * 0.05 + 1000 * 2.5) / 1e6)

    def test_reread_writes_at_the_carrying_requests_ttl(self):
        p = pricing.Pricer(cfg())
        five = req(cache_write=100)
        hour = req(cache_write=100, cache_write_1h=100)
        self.assertAlmostEqual(p.reread(five, 5000, 50), 5000 * 1.25 + 5000 * 0.05 * 50)  # 18,750 (A1)
        self.assertAlmostEqual(p.reread(hour, 5000, 50), 5000 * 2 + 5000 * 0.05 * 50)
        self.assertAlmostEqual(p.reread_usd(hour, 5000, 50), (5000 * 8 + 5000 * 0.2 * 50) / 1e6)
        self.assertIsNone(p.reread_usd(req(model="gpt-5"), 5000, 50))


class TestConfig(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"TOKNIX_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = Path(tmp.name)

    def test_user_prices_add_to_the_defaults(self):
        (self.home / "config.json").write_text(json.dumps({"prices": {"my-model": [1, 1, 1, 1, 1]}}))
        p = pricing.Pricer(config.load())
        self.assertEqual(p.rates("my-model"), [1, 1, 1, 1, 1])
        self.assertEqual(p.rates("claude-opus-5-5"), OPUS_55)

    def test_broken_entries_are_unpriced_not_fatal(self):
        (self.home / "config.json").write_text(json.dumps({"prices": {
            "a-model": [1, 2], "b-model": "cheap", "c-model": [1, 1, 1, 1, "x"], "d-model": {"prices": [1]},
            "claude-haiku-5-5": {"prices": [1, 1, 1, 1, 1], "above": "big", "prices_above": [9]}},
            "price_multipliers": {"fast": "x"}}))
        p = pricing.Pricer(config.load())
        for model in ("a-model", "b-model", "c-model", "d-model"):
            with self.subTest(model=model):
                self.assertIsNone(p.rates(model))
        self.assertEqual(p.rates("claude-haiku-5-5", 500000), [1, 1, 1, 1, 1])  # a broken tier is ignored
        self.assertEqual(p.rates("claude-opus-5-5", speed="fast"), OPUS_55)


class TestFormat(unittest.TestCase):
    def test_fmt_usd(self):
        self.assertEqual(pricing.fmt_usd(4371.4), "$4,371")
        self.assertEqual(pricing.fmt_usd(12.346), "$12.35")
        self.assertEqual(pricing.fmt_usd(0.056), "$0.056")
        self.assertEqual(pricing.fmt_usd(0), "$0.00")
        self.assertEqual(pricing.fmt_usd(None), "—")


if __name__ == "__main__":
    unittest.main()
