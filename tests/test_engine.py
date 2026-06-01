"""Offline tests for the scoring engine — no network required."""

from financial_mcp import engine


def test_normalize_clamps_and_scales():
    assert engine.normalize(5, 0, 10) == 0.5
    assert engine.normalize(-1, 0, 10) == 0.0   # clamped low
    assert engine.normalize(99, 0, 10) == 1.0    # clamped high
    assert engine.normalize(5, 5, 5) == 0.5      # degenerate range -> neutral


def test_percentile_rank():
    assert engine.percentile_rank(5, []) == 50.0          # no context -> neutral
    # (B + 0.5*E) / N * 100 -> (2 + 0.5) / 4 * 100
    assert engine.percentile_rank(10, [0, 5, 10, 15]) == 62.5
    assert engine.percentile_rank(100, [0, 5, 10]) == 100.0
    assert engine.percentile_rank(-1, [0, 5, 10]) == 0.0


def test_score_ticker_no_data_is_neutral():
    result = engine.score_ticker(
        symbol="TEST",
        fundamentals=None,
        momentum=None,
        all_momentum=[],
        sector_medians=None,
    )
    assert result["symbol"] == "TEST"
    # With no valuation/momentum/sentiment signal there is nothing to score, so
    # the result must be a neutral 50 (not 100 from an inverted zero risk
    # penalty). Regression guard for the fix shipped in v0.1.8.
    assert result["score"] == 50.0


def test_score_ticker_bounded_with_synthetic_data():
    fundamentals = {
        "sector": "Technology",
        "pe_ratio": 15,
        "ev_to_ebitda": 10,
        "price_to_book": 3,
        "dividend_yield": 2.0,
        "market_cap": 1e11,
    }
    momentum = {
        "price_momentum_30d": 0.05,
        "price_momentum_90d": 0.10,
        "relative_strength": 1.2,
        "volatility": 0.2,
    }
    sector_medians = {"Technology": {"median_pe": 25, "median_ev_ebitda": 18}}
    result = engine.score_ticker(
        symbol="AAA",
        fundamentals=fundamentals,
        momentum=momentum,
        all_momentum=[momentum],
        sector_medians=sector_medians,
    )
    assert 0.0 <= result["score"] <= 100.0
    assert result["valuation"] is not None
    assert result["momentum"] is not None


def test_score_universe_empty():
    assert engine.score_universe([]) == []
