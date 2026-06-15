from app.llm.telemetry import CostTracker, cost_usd, price_for


def test_price_lookup_known_and_default():
    assert price_for("claude-opus-4-8") == (5.0, 25.0)
    assert price_for("claude-fable-5") == (10.0, 50.0)
    # Unknown model falls back to Opus-tier pricing.
    assert price_for("some-future-model") == (5.0, 25.0)


def test_cost_usd_basic():
    # 1M input @ $5, 1M output @ $25 = $30.
    assert cost_usd("claude-opus-4-8", 1_000_000, 1_000_000) == 30.0


def test_cost_usd_cache_discount():
    # Cache reads bill at 0.1x input price.
    cost = cost_usd("claude-opus-4-8", 0, 0, cache_read_tokens=1_000_000)
    assert round(cost, 2) == 0.5


def test_tracker_accumulates():
    t = CostTracker()
    t.record("security", "claude-opus-4-8", input_tokens=1000, output_tokens=500)
    t.record("judge", "claude-opus-4-8", input_tokens=2000, output_tokens=100)
    assert len(t.events) == 2
    assert t.total_input_tokens == 3000
    assert t.total_output_tokens == 600
    assert t.total_cost > 0
    summary = t.summary()
    assert summary["calls"] == 2
    assert len(summary["by_component"]) == 2
