"""Offline tests for the company-intelligence parsers — no network required."""

import numpy as np
import pandas as pd

from financial_mcp import company


# ---------------------------------------------------------------------------
# parse_news_item
# ---------------------------------------------------------------------------


def test_parse_news_item_flat_shape():
    item = {
        "title": "Apple beats earnings",
        "publisher": "Reuters",
        "link": "https://example.com/a",
        "providerPublishTime": 1717200000,
        "summary": "Strong quarter.",
    }
    parsed = company.parse_news_item(item)
    assert parsed["title"] == "Apple beats earnings"
    assert parsed["publisher"] == "Reuters"
    assert parsed["url"] == "https://example.com/a"
    assert parsed["published"].startswith("2024-06-01")
    assert parsed["summary"] == "Strong quarter."


def test_parse_news_item_nested_shape():
    item = {
        "id": "abc",
        "content": {
            "title": "NVDA hits new high",
            "pubDate": "2026-06-01T12:00:00Z",
            "summary": "Chips.",
            "provider": {"displayName": "Bloomberg"},
            "canonicalUrl": {"url": "https://example.com/b"},
        },
    }
    parsed = company.parse_news_item(item)
    assert parsed["title"] == "NVDA hits new high"
    assert parsed["publisher"] == "Bloomberg"
    assert parsed["published"] == "2026-06-01T12:00:00Z"
    assert parsed["url"] == "https://example.com/b"


def test_parse_news_item_garbage():
    assert company.parse_news_item({"foo": "bar"}) is None
    assert company.parse_news_item("not a dict") is None
    assert company.parse_news_item({"content": {"pubDate": "x"}}) is None


# ---------------------------------------------------------------------------
# parse_earnings_dates
# ---------------------------------------------------------------------------


def _earnings_df():
    dates = pd.to_datetime([
        "2026-09-15", "2026-07-20",                      # future
        "2026-04-20", "2026-01-20", "2025-10-20",        # past
        "2025-07-21", "2025-04-21",
    ])
    return pd.DataFrame(
        {
            "EPS Estimate": [2.5, 2.4, 2.3, np.nan, 2.1, 2.0, 1.9],
            "Reported EPS": [np.nan, np.nan, 2.35, 2.25, 2.18, 1.95, 1.85],
            "Surprise(%)": [np.nan, np.nan, 2.17, np.nan, 3.81, -2.5, -2.63],
        },
        index=dates,
    )


def test_parse_earnings_dates_split_and_cap():
    now = pd.Timestamp("2026-06-10")
    result = company.parse_earnings_dates(_earnings_df(), now)

    assert result["next_earnings"] == {"date": "2026-07-20", "eps_estimate": 2.4}

    recent = result["recent"]
    assert len(recent) == 4  # capped, 5 past rows available
    assert recent[0]["date"] == "2026-04-20"  # most recent first
    assert recent[0]["eps_actual"] == 2.35
    assert recent[1]["eps_estimate"] is None  # NaN -> None
    assert recent[1]["surprise_pct"] is None


def test_parse_earnings_dates_empty():
    assert company.parse_earnings_dates(None, pd.Timestamp("2026-06-10")) == {
        "next_earnings": None,
        "recent": [],
    }
    empty = pd.DataFrame()
    assert company.parse_earnings_dates(empty, pd.Timestamp("2026-06-10")) == {
        "next_earnings": None,
        "recent": [],
    }


def test_parse_earnings_dates_tz_aware_index():
    df = _earnings_df()
    df.index = df.index.tz_localize("America/New_York")
    result = company.parse_earnings_dates(df, pd.Timestamp("2026-06-10"))
    assert result["next_earnings"]["date"] == "2026-07-20"


# ---------------------------------------------------------------------------
# parse_recommendation_trend
# ---------------------------------------------------------------------------


def test_parse_recommendation_trend():
    df = pd.DataFrame({
        "period": ["0m", "-1m", "-2m", "-3m", "-4m"],
        "strongBuy": [10, 9, np.nan, 8, 8],
        "buy": [20, 21, 22, 20, 19],
        "hold": [5, 5, 6, 7, 7],
        "sell": [1, 1, 1, 2, 2],
        "strongSell": [0, 0, 0, 1, 1],
    })
    trend = company.parse_recommendation_trend(df)
    assert len(trend) == 4  # capped
    assert trend[0] == {
        "period": "0m", "strong_buy": 10, "buy": 20,
        "hold": 5, "sell": 1, "strong_sell": 0,
    }
    assert trend[2]["strong_buy"] == 0  # NaN -> 0


def test_parse_recommendation_trend_empty():
    assert company.parse_recommendation_trend(None) == []
    assert company.parse_recommendation_trend(pd.DataFrame()) == []


# ---------------------------------------------------------------------------
# compute_options_metrics
# ---------------------------------------------------------------------------


def _chain():
    calls = pd.DataFrame({
        "strike": [95.0, 100.0, 105.0],
        "volume": [100, 200, np.nan],
        "openInterest": [1000, 2000, 500],
        "impliedVolatility": [0.30, 0.25, 0.28],
    })
    puts = pd.DataFrame({
        "strike": [95.0, 100.0, 105.0],
        "volume": [150, 250, 50],
        "openInterest": [800, 1500, 200],
        "impliedVolatility": [0.32, 0.27, 0.31],
    })
    return calls, puts


def test_compute_options_metrics_ratios_and_atm_iv():
    calls, puts = _chain()
    m = company.compute_options_metrics(calls, puts, spot=101.0)

    assert m["call_volume"] == 300  # NaN treated as 0
    assert m["put_volume"] == 450
    assert m["call_oi"] == 3500
    assert m["put_oi"] == 2500
    assert m["put_call_volume_ratio"] == 1.5
    assert m["put_call_oi_ratio"] == round(2500 / 3500, 4)
    # Nearest strike to 101 is 100 for both legs -> mean(0.25, 0.27).
    assert m["atm_iv"] == 0.26


def test_compute_options_metrics_max_pain():
    calls, puts = _chain()
    m = company.compute_options_metrics(calls, puts, spot=101.0)
    # Hand-check: payouts at 95/100/105:
    #   95:  calls 0;                puts 800*0 + 1500*5 + 200*10 = 9500 -> 9500
    #   100: calls 1000*5 = 5000;    puts 200*5 = 1000             -> 6000
    #   105: calls 1000*10+2000*5 = 20000; puts 0                  -> 20000
    assert m["max_pain"] == 100.0


def test_compute_options_metrics_zero_oi_and_empty():
    calls, puts = _chain()
    calls["openInterest"] = 0
    puts["openInterest"] = 0
    m = company.compute_options_metrics(calls, puts, spot=101.0)
    assert m["max_pain"] is None

    empty = pd.DataFrame()
    m = company.compute_options_metrics(empty, empty, spot=None)
    assert m["call_volume"] == 0
    assert m["put_volume"] == 0
    assert m["put_call_volume_ratio"] is None
    assert m["put_call_oi_ratio"] is None
    assert m["atm_iv"] is None
    assert m["max_pain"] is None
