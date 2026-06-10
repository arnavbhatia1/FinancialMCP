"""Offline tests for the technicals layer — no network required."""

import numpy as np
import pandas as pd

from financial_mcp import technicals


def _make_hist(closes, volumes=None) -> pd.DataFrame:
    """Synthetic OHLCV frame: High/Low bracket Close, Open = prior Close."""
    closes = pd.Series([float(c) for c in closes])
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    index = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": closes.shift(1).fillna(closes.iloc[0]).values,
            "High": (closes * 1.01).values,
            "Low": (closes * 0.99).values,
            "Close": closes.values,
            "Volume": volumes,
        },
        index=index,
    )


def _constant_hist(price=100.0, n=260) -> pd.DataFrame:
    """Flat series where High == Low == Close (zero true range / bandwidth)."""
    index = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": [price] * n,
            "High": [price] * n,
            "Low": [price] * n,
            "Close": [price] * n,
            "Volume": [1_000_000] * n,
        },
        index=index,
    )


# ---------------------------------------------------------------------------
# compute_indicators
# ---------------------------------------------------------------------------


def test_rsi_extremes():
    rising = _make_hist(np.linspace(100, 200, 120))
    falling = _make_hist(np.linspace(200, 100, 120))
    assert technicals.compute_indicators(rising)["rsi_14"] > 70
    assert technicals.compute_indicators(falling)["rsi_14"] < 30


def test_constant_series_sma_percent_b_and_atr():
    price = 100.0
    result = technicals.compute_indicators(_constant_hist(price))
    assert result["sma_20"] == price
    assert result["sma_50"] == price
    assert result["sma_200"] == price
    # upper == lower -> zero bandwidth -> percent_b must be None, not a crash.
    assert result["bollinger"]["percent_b"] is None
    assert result["atr_14"] == 0.0


def test_pct_from_52w_high_zero_at_high():
    result = technicals.compute_indicators(_make_hist(np.linspace(100, 200, 120)))
    assert result["pct_from_52w_high"] == 0.0
    assert result["pct_from_52w_low"] > 0


def test_macd_crossover_labels():
    up = technicals.compute_indicators(_make_hist(np.linspace(100, 200, 120)))
    down = technicals.compute_indicators(_make_hist(np.linspace(200, 100, 120)))
    assert up["macd"]["crossover"] == "bullish"
    assert down["macd"]["crossover"] == "bearish"


def test_sma200_none_when_short():
    result = technicals.compute_indicators(_make_hist(np.linspace(100, 120, 60)))
    assert result["sma_200"] is None
    assert result["price_above_sma200"] is None
    assert result["golden_cross"] is None


def test_summary_non_empty_strings():
    result = technicals.compute_indicators(_make_hist(np.linspace(100, 200, 120)))
    summary = result["summary"]
    assert summary  # non-empty
    assert all(isinstance(line, str) and line for line in summary)


# ---------------------------------------------------------------------------
# compute_returns
# ---------------------------------------------------------------------------


def test_compute_returns_known_values():
    # 100 bars: price = 100 + bar index, last close = 199.
    series = pd.Series(
        [100.0 + i for i in range(100)],
        index=pd.date_range("2024-01-02", periods=100, freq="B"),
    )
    returns = technicals.compute_returns(series)
    assert returns["return_1d"] == round(199 / 198 - 1, 4)
    assert returns["return_5d"] == round(199 / 194 - 1, 4)
    assert returns["return_1mo"] == round(199 / 178 - 1, 4)
    assert returns["return_3mo"] == round(199 / 136 - 1, 4)


def test_compute_returns_short_series():
    series = pd.Series(
        [100.0, 101.0, 102.0],
        index=pd.date_range("2024-01-02", periods=3, freq="B"),
    )
    returns = technicals.compute_returns(series)
    assert returns["return_1d"] == round(102 / 101 - 1, 4)
    assert returns["return_5d"] is None
    assert returns["return_1mo"] is None
    assert returns["return_3mo"] is None


# ---------------------------------------------------------------------------
# get_price_history validation (no network — rejected before any fetch)
# ---------------------------------------------------------------------------


def test_get_price_history_invalid_period():
    result = technicals.get_price_history("SPY", period="7mo")
    assert isinstance(result, dict)
    assert "error" in result
    assert "7mo" in result["error"]


def test_get_price_history_invalid_interval():
    result = technicals.get_price_history("SPY", period="3mo", interval="2h")
    assert isinstance(result, dict)
    assert "error" in result
