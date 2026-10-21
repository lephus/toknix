"""Prices per model and ETU (A1, P8-T2).

A request's tokens are priced at its own model's list price, then counted in input tokens of `etu_model`, so
ETU adds up across models and is proportional to $. A model without a price (Codex, Gemini, a model newer than
the table) falls back to `weights`, and has no $.
"""
from __future__ import annotations

from toknix.config import DEFAULTS

KINDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")  # order of a price list
BOUNDARY = "-@[:"  # what may follow a price key in a model id: a version or date, Vertex's @, `[1m]`, Bedrock's :0


def tokens(r) -> list[int]:
    """The 5 kinds of a request, in the order of a price list."""
    one_hour = min(r.cache_write_1h, r.cache_write)
    return [r.input, r.cache_write - one_hour, one_hour, r.cache_read, r.output]


def _price_list(value) -> list | None:
    if isinstance(value, list) and len(value) == len(KINDS) and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0 for x in value):
        return value
    return None


def _entry(value) -> tuple | None:
    """(prices, prompt threshold or None, prices above it) from a config entry; anything broken is no price."""
    if isinstance(value, dict):
        base, above, high = _price_list(value.get("prices")), value.get("above"), _price_list(value.get("prices_above"))
        if base is None:
            return None
        if isinstance(above, (int, float)) and not isinstance(above, bool) and high is not None:
            return base, above, high
        return base, None, None
    base = _price_list(value)
    return (base, None, None) if base is not None else None


class Pricer:
    def __init__(self, cfg: dict):
        table = dict(DEFAULTS["prices"])
        if isinstance(cfg.get("prices"), dict):
            table.update(cfg["prices"])  # config.json adds models to the defaults (null removes one)
        self._table = {k: e for k, e in ((k, _entry(v)) for k, v in table.items()) if isinstance(k, str) and e}
        self._keys = sorted(self._table, key=len, reverse=True)
        self._found: dict[str, tuple | None] = {}
        mult = cfg.get("price_multipliers")
        self._mult = {k: float(v) for k, v in mult.items() if isinstance(v, (int, float))
                      and not isinstance(v, bool) and v > 0} if isinstance(mult, dict) else {}
        w = [float(x) for x in cfg["weights"]]
        self.fallback = [w[0], w[1], w[1], w[2], w[3]]
        self.etu_model = cfg.get("etu_model") if isinstance(cfg.get("etu_model"), str) else ""
        base = self.rates(self.etu_model) if self.etu_model else None
        self.ref = base[0] if base and base[0] > 0 else None  # $/MTok of one ETU; None: ETU is `weights`

    def _find(self, model: str) -> tuple | None:
        if model not in self._found:
            i = model.find("claude-")
            ident = model[i:] if i >= 0 else model  # Bedrock: us.anthropic.claude-…
            self._found[model] = next((self._table[k] for k in self._keys if ident == k or (
                ident.startswith(k) and ident[len(k)] in BOUNDARY)), None)
        return self._found[model]

    def rates(self, model: str, prompt: int = 0, speed: str = "", geo: str = "") -> list | None:
        """$/MTok of each kind for this model, prompt length (input + cache read + cache write), speed and
        inference geography; None when the model has no price."""
        e = self._find(model) if isinstance(model, str) else None
        if e is None:
            return None
        base, above, high = e
        p = high if above is not None and prompt > above else base
        m = self._mult.get(speed, 1.0) * self._mult.get(geo, 1.0)
        return [x * m for x in p] if m != 1 else list(p)

    def weights(self, model: str, prompt: int = 0, speed: str = "", geo: str = "") -> list[float]:
        """ETU per token of each kind (A1: w(r))."""
        p = self.rates(model, prompt, speed, geo) if self.ref else None
        return [x / self.ref for x in p] if p else list(self.fallback)

    # ----------------------------------------------------------------- per request (transcripts.Request)
    def cost(self, r) -> tuple[float, float | None, float]:
        """(ETU, $ or None, ETU of its output tokens)."""
        u = tokens(r)
        p = self.rates(r.model, r.context, r.speed, r.geo)
        w = [x / self.ref for x in p] if p and self.ref else list(self.fallback)
        usd = sum(x * t for x, t in zip(p, u)) / 1e6 if p else None
        return sum(x * t for x, t in zip(w, u)), usd, w[4] * r.output

    def etu(self, r) -> float:
        return self.cost(r)[0]

    def usd(self, r) -> float | None:
        return self.cost(r)[1]

    def output_etu(self, r) -> float:
        return self.cost(r)[2]

    def reread(self, r, t: float, left: int) -> float:
        """reread_cost (A1): t tokens written to the cache by r, at r's TTL, then read by `left` requests."""
        w = self.weights(r.model, r.context, r.speed, r.geo)
        return t * w[2 if r.cache_write_1h > 0 else 1] + t * w[3] * left

    def reread_usd(self, r, t: float, left: int) -> float | None:
        p = self.rates(r.model, r.context, r.speed, r.geo)
        return (t * p[2 if r.cache_write_1h > 0 else 1] + t * p[3] * left) / 1e6 if p else None

    def write_loss(self, r) -> tuple[float, float | None]:
        """ETU and $ a cache break costs (A6.5): r's writes, less what reading them would have cost."""
        u = tokens(r)
        w = self.weights(r.model, r.context, r.speed, r.geo)
        p = self.rates(r.model, r.context, r.speed, r.geo)
        etu = w[1] * u[1] + w[2] * u[2] - w[3] * r.cache_write
        return etu, (p[1] * u[1] + p[2] * u[2] - p[3] * r.cache_write) / 1e6 if p else None


def fmt_usd(x: float | None) -> str:
    """$4,371 · $12.35 · $0.056; None (no price) is a dash."""
    if x is None:
        return "—"
    if abs(x) >= 100:
        return f"${x:,.0f}"
    if abs(x) >= 1 or x == 0:
        return f"${x:,.2f}"
    return f"${x:.3f}"
