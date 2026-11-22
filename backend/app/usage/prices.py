"""USD per million tokens (input, output, cache write, cache read), matched by
model-id prefix; the longest prefix wins. A running estimate only: at finish a
step's figure becomes the result's total_cost_usd."""

PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-fable-5-2": (8, 40, 10, 0.20),
    "claude-fable-5-1": (10, 50, 12.5, 1),
    "claude-fable-5": (10, 50, 12.5, 1),
    "claude-opus-5-5": (4, 20, 5, 0.20),
    "claude-opus-5": (5, 25, 6.25, 0.50),
    "claude-opus-4-8": (5, 25, 6.25, 0.50),
    "claude-opus-4-7": (5, 25, 6.25, 0.50),
    "claude-opus-4-6": (5, 25, 6.25, 0.50),
    "claude-sonnet-5-5": (2, 10, 2.5, 0.10),
    "claude-sonnet-5": (2, 10, 2.5, 0.20),
    "claude-sonnet-4-6": (3, 15, 3.75, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-haiku-4-5": (1, 5, 1.25, 0.10),
}


def price_for(model: str | None) -> tuple[float, float, float, float] | None:
    if not model:
        return None
    best = max((k for k in PRICES if model.startswith(k)), key=len, default=None)
    return PRICES[best] if best else None


def estimate(model: str | None, input: int, output: int, cache_write: int, cache_read: int) -> float | None:
    """The USD cost of these tokens on ``model``; None for an unknown model."""
    p = price_for(model)
    if p is None:
        return None
    return (input * p[0] + output * p[1] + cache_write * p[2] + cache_read * p[3]) / 1_000_000
