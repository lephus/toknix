"""Sample suite for the P1-T4 end-to-end test: 40 tests pass, 2 fail on purpose.

Not part of tokopt's own suite (no __init__.py, so `unittest discover -s tests` skips this folder).
Run it with `python -m pytest tests/fixtures_py` or `python -m unittest discover -s tests/fixtures_py`.
"""
import unittest


def cart_total(prices, discount=0):
    return sum(prices) - discount


def parse_qty(text):
    return int(text.strip())


class TestCartTotal(unittest.TestCase):
    def test_fixed_discount(self):
        self.assertEqual(cart_total([40, 60], discount=10), 80)  # fails on purpose: 90 != 80


class TestParseQty(unittest.TestCase):
    def test_missing_qty(self):
        self.assertEqual(parse_qty(""), 0)  # fails on purpose: ValueError


class TestPasses(unittest.TestCase):
    pass


for _i in range(40):
    setattr(TestPasses, f"test_total_{_i:02d}",
            lambda self, i=_i: self.assertEqual(cart_total([i, 1]), i + 1))


if __name__ == "__main__":
    unittest.main()
