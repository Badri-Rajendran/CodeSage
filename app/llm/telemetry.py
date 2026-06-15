"""Token-cost telemetry.

Every Claude call is metered here. Prices are per 1M tokens (USD), sourced from
the Claude pricing table. Cache reads bill at ~0.1x input; 5-minute cache writes
at ~1.25x input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Per 1M tokens (USD): (input, output).
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Fallback price if a model isn't in the table (assume Opus-tier).
_DEFAULT_PRICE = (5.0, 25.0)

_CACHE_READ_MULT = 0.1
_CACHE_WRITE_MULT = 1.25


def price_for(model: str) -> tuple[float, float]:
    return PRICES.get(model, _DEFAULT_PRICE)


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Compute the USD cost of a single call."""
    in_price, out_price = price_for(model)
    return (
        input_tokens * in_price
        + output_tokens * out_price
        + cache_read_tokens * in_price * _CACHE_READ_MULT
        + cache_write_tokens * in_price * _CACHE_WRITE_MULT
    ) / 1_000_000


@dataclass
class UsageEvent:
    component: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0

    def as_dict(self) -> dict:
        return {
            "component": self.component,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": round(self.cost, 6),
        }


@dataclass
class CostTracker:
    """Accumulates usage across a single review/eval run."""

    events: list[UsageEvent] = field(default_factory=list)

    def record(
        self,
        component: str,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> UsageEvent:
        ev = UsageEvent(
            component=component,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost=cost_usd(
                model,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
            ),
        )
        self.events.append(ev)
        return ev

    @property
    def total_cost(self) -> float:
        return round(sum(e.cost for e in self.events), 6)

    @property
    def total_input_tokens(self) -> int:
        return sum(e.input_tokens for e in self.events)

    @property
    def total_output_tokens(self) -> int:
        return sum(e.output_tokens for e in self.events)

    def summary(self) -> dict:
        return {
            "total_cost_usd": self.total_cost,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "calls": len(self.events),
            "by_component": [e.as_dict() for e in self.events],
        }
