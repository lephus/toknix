"""Sample module for `tokopt outline` (P3-T1).

The text below is not code:
    def not_a_function():
        class NotAClass: ...
"""
from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    def typed_only(x: int) -> str: ...

try:
    from fast_json import dumps
except ImportError:  # pragma: no cover
    def dumps(obj, *, indent=None):
        return repr(obj)


def retry(times: int = 3):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for _ in range(times):
                try:
                    return fn(*args, **kwargs)
                except OSError:
                    pass
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def one_liner(a, b): return a + b


@retry(
    times=5,
)
async def fetch(
    url: str,              # where: host:port/path
    timeout: float = 2.5,
    *,
    headers: dict[str, str] | None = None,
    sep: str = "a:b",
) -> bytes:
    """Fetch url."""
    return b""


class Base:
    pass


class Shop(Base, metaclass=type):
    """A shop."""

    TAX = 0.1
    square = staticmethod(lambda x: x * x)

    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def title(self) -> str:
        return self.name.title()

    @staticmethod
    @functools.lru_cache(maxsize=None)
    def tax_for(amount: float) -> float:
        return amount * Shop.TAX

    async def checkout(self, cart: list[int]) -> int:
        def subtotal() -> int:
            return sum(cart)

        return subtotal()

    class Item:
        def __init__(self, sku: str):
            self.sku = sku

        class Meta:
            ordering = ["sku"]

            def label(self): return "item"

    def __repr__(self): return f"Shop({self.name!r})"


class EmptyError(Exception): pass
