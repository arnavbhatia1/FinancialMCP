"""Live data-layer smoke tests — hit real third-party APIs.

Opt-in: these are marked `network` and skipped by the default `pytest` run
(see pyproject `addopts`). Run them with `pytest -m network`. They guard
against the class of silent breakage where an upstream API or schema changes
and a tool quietly returns empty data (e.g. the Treasury field/endpoint bugs
fixed in v0.1.8).
"""

import pytest

from financial_mcp import market_data, treasury, regime, sec_edgar

pytestmark = pytest.mark.network


def test_price_is_live():
    price = market_data.get_current_price("AAPL")
    assert isinstance(price, (int, float)) and price > 0


def test_treasury_rates_not_empty():
    result = treasury.get_treasury_rates(30)
    assert result is not None
    rates = result.get("rates", [])
    assert rates, "Treasury avg-interest-rates returned no rows (field/endpoint regression?)"
    assert any(r.get("avg_interest_rate") is not None for r in rates)


def test_treasury_yield_curve_not_empty():
    rows = treasury.get_yield_curve_daily(5)
    assert rows, "Daily yield curve returned no rows (endpoint regression?)"
    newest = rows[0]
    assert "date" in newest
    assert newest.get("10yr") is not None


def test_detect_regime_returns_classification():
    result = regime.detect_regime()
    assert result is not None
    assert result.get("regime") in {
        "BULL", "BEAR", "SIDEWAYS", "HIGH_VOLATILITY", "CRASH",
    }


def test_sec_filings_live():
    filings = sec_edgar.get_filings("AAPL", "10-K", 1)
    assert filings, "SEC EDGAR returned no filings"
    assert filings[0].get("filing_type") == "10-K"
