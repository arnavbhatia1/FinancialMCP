"""Company-level event and expectation data via yfinance.

Covers news headlines, earnings dates/estimates, analyst ratings and price
targets, and a single-expiry options snapshot (put/call ratios, ATM IV,
max pain).

Every public fetch function catches exceptions internally, logs via
``logger.exception``, and returns None -- callers never need to handle
yfinance errors. Pure parsing/computation helpers (no network) are kept
separate so tests can run offline against synthetic inputs.
"""

import logging
from datetime import datetime, timezone

import pandas as pd

from . import market_data
from .utils import safe_round

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers (no network) -- unit-tested offline with synthetic inputs
# ---------------------------------------------------------------------------


def _nan_to_none(value):
    """Map NaN/NaT (and None) to None, pass everything else through."""
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def parse_news_item(item: dict) -> dict | None:
    """Normalize one yfinance news item to a flat dict, or None if unusable.

    Handles both shapes yfinance has shipped:
    - flat: {"title", "publisher", "link", "providerPublishTime" (epoch secs)}
    - nested: {"id", "content": {"title", "pubDate" (ISO), "summary",
      "provider": {"displayName"}, "canonicalUrl": {"url"}}}
    """
    try:
        if not isinstance(item, dict):
            return None

        content = item.get("content")
        if isinstance(content, dict):
            title = content.get("title")
            if not title:
                return None
            provider = content.get("provider")
            canonical = content.get("canonicalUrl")
            return {
                "title": title,
                "publisher": provider.get("displayName") if isinstance(provider, dict) else None,
                "published": content.get("pubDate"),
                "url": canonical.get("url") if isinstance(canonical, dict) else None,
                "summary": content.get("summary"),
            }

        title = item.get("title")
        if not title:
            return None
        published = None
        ts = item.get("providerPublishTime")
        if ts is not None:
            try:
                published = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                published = None
        return {
            "title": title,
            "publisher": item.get("publisher"),
            "published": published,
            "url": item.get("link"),
            "summary": item.get("summary"),
        }
    except Exception:
        logger.exception("parse_news_item failed")
        return None


def _naive_timestamp(value):
    """Coerce to a tz-naive pandas Timestamp (drop tz info), or None."""
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)
        return ts
    except Exception:
        return None


def parse_earnings_dates(df, now) -> dict:
    """Split a yfinance ``earnings_dates`` DataFrame around *now*.

    *df* is indexed by timestamp with columns like "EPS Estimate",
    "Reported EPS", "Surprise(%)". *now* is passed in (any
    Timestamp-coercible value) so tests can fix time; comparison is done
    on tz-naive timestamps so tz-aware indexes are safe.

    Returns {"next_earnings": {"date", "eps_estimate"} | None,
             "recent": up to 4 most-recent past rows
             [{"date", "eps_estimate", "eps_actual", "surprise_pct"}]}.
    """
    result: dict = {"next_earnings": None, "recent": []}
    if df is None or getattr(df, "empty", True):
        return result

    now_ts = _naive_timestamp(now)
    if now_ts is None:
        return result

    rows = []
    for idx, row in df.iterrows():
        ts = _naive_timestamp(idx)
        if ts is not None:
            rows.append((ts, row))
    rows.sort(key=lambda pair: pair[0])

    future = [(ts, row) for ts, row in rows if ts >= now_ts]
    past = [(ts, row) for ts, row in rows if ts < now_ts]

    if future:
        ts, row = future[0]
        result["next_earnings"] = {
            "date": ts.date().isoformat(),
            "eps_estimate": safe_round(_nan_to_none(row.get("EPS Estimate"))),
        }

    for ts, row in reversed(past[-4:]):  # most recent first, capped at 4
        result["recent"].append({
            "date": ts.date().isoformat(),
            "eps_estimate": safe_round(_nan_to_none(row.get("EPS Estimate"))),
            "eps_actual": safe_round(_nan_to_none(row.get("Reported EPS"))),
            "surprise_pct": safe_round(_nan_to_none(row.get("Surprise(%)"))),
        })
    return result


_TREND_COLUMNS = {
    "strong_buy": "strongBuy",
    "buy": "buy",
    "hold": "hold",
    "sell": "sell",
    "strong_sell": "strongSell",
}


def parse_recommendation_trend(df) -> list:
    """Parse a yfinance recommendations(-summary) DataFrame.

    Expects columns period/strongBuy/buy/hold/sell/strongSell (period may
    live in the index instead). Returns up to 4 rows of
    {"period", "strong_buy", "buy", "hold", "sell", "strong_sell"}.
    """
    if df is None or getattr(df, "empty", True):
        return []
    try:
        trend = []
        has_period_col = "period" in df.columns
        for idx, row in df.head(4).iterrows():
            period = row.get("period") if has_period_col else idx
            entry = {"period": str(period)}
            for out_key, col in _TREND_COLUMNS.items():
                value = _nan_to_none(row.get(col))
                entry[out_key] = int(value) if value is not None else 0
            trend.append(entry)
        return trend
    except Exception:
        logger.exception("parse_recommendation_trend failed")
        return []


def _column_sum(df, column: str) -> int:
    """Sum a numeric column with NaN treated as 0; 0 for missing/empty."""
    if df is None or getattr(df, "empty", True) or column not in df.columns:
        return 0
    return int(df[column].fillna(0).sum())


def _strike_oi_pairs(df) -> list:
    """(strike, open_interest) pairs with NaN OI treated as 0."""
    if df is None or getattr(df, "empty", True) or "strike" not in df.columns:
        return []
    if "openInterest" in df.columns:
        oi = df["openInterest"].fillna(0)
    else:
        oi = pd.Series(0.0, index=df.index)
    return [
        (float(strike), float(open_interest))
        for strike, open_interest in zip(df["strike"], oi)
        if not pd.isna(strike)
    ]


def _nearest_strike_iv(df, spot: float) -> float | None:
    """Implied volatility of the row whose strike is nearest *spot*."""
    if df is None or getattr(df, "empty", True):
        return None
    if "strike" not in df.columns or "impliedVolatility" not in df.columns:
        return None
    idx = (df["strike"] - spot).abs().idxmin()
    iv = _nan_to_none(df.loc[idx, "impliedVolatility"])
    return float(iv) if iv is not None else None


def compute_options_metrics(calls_df, puts_df, spot: float | None) -> dict:
    """Aggregate metrics from one expiry's calls/puts chains (pure, no network).

    Returns {"call_volume", "put_volume", "call_oi", "put_oi",
    "put_call_volume_ratio", "put_call_oi_ratio", "atm_iv", "max_pain"}.
    Ratios are None when the denominator is 0; max_pain is None when all
    open interest is zero; atm_iv is None without a usable spot/IV.
    """
    call_volume = _column_sum(calls_df, "volume")
    put_volume = _column_sum(puts_df, "volume")
    call_oi = _column_sum(calls_df, "openInterest")
    put_oi = _column_sum(puts_df, "openInterest")

    put_call_volume_ratio = safe_round(put_volume / call_volume) if call_volume else None
    put_call_oi_ratio = safe_round(put_oi / call_oi) if call_oi else None

    # ATM implied volatility: mean IV of the call and put nearest the spot.
    atm_iv = None
    if spot is not None:
        ivs = [
            iv
            for iv in (_nearest_strike_iv(calls_df, spot), _nearest_strike_iv(puts_df, spot))
            if iv is not None
        ]
        if ivs:
            atm_iv = safe_round(sum(ivs) / len(ivs))

    # Max pain: the settlement strike minimizing total intrinsic payout to
    # option holders, weighted by open interest, over the union of strikes.
    max_pain = None
    call_pairs = _strike_oi_pairs(calls_df)
    put_pairs = _strike_oi_pairs(puts_df)
    total_oi = sum(oi for _, oi in call_pairs) + sum(oi for _, oi in put_pairs)
    if total_oi > 0:
        strikes = sorted({s for s, _ in call_pairs} | {s for s, _ in put_pairs})
        best_strike, best_payout = None, None
        for settle in strikes:
            payout = sum(oi * max(0.0, settle - strike) for strike, oi in call_pairs)
            payout += sum(oi * max(0.0, strike - settle) for strike, oi in put_pairs)
            if best_payout is None or payout < best_payout:
                best_strike, best_payout = settle, payout
        max_pain = best_strike

    return {
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "put_call_volume_ratio": put_call_volume_ratio,
        "put_call_oi_ratio": put_call_oi_ratio,
        "atm_iv": atm_iv,
        "max_pain": max_pain,
    }


def _calendar_next_earnings(cal) -> str | None:
    """Extract the next earnings date (ISO string) from Ticker.calendar.

    Modern yfinance returns a dict with an "Earnings Date" list of date
    objects; older versions returned a DataFrame. Returns None when the
    shape is unrecognized or empty.
    """
    try:
        if cal is None:
            return None
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date")
            if not dates:
                return None
            if not isinstance(dates, (list, tuple)):
                dates = [dates]
            return _date_to_iso(dates[0]) if dates else None
        # Old yfinance: DataFrame with an "Earnings Date" row.
        index = getattr(cal, "index", None)
        if index is not None and "Earnings Date" in index:
            row = cal.loc["Earnings Date"]
            values = list(row.dropna()) if hasattr(row, "dropna") else [row]
            return _date_to_iso(values[0]) if values else None
    except Exception:
        logger.debug("calendar parse failed", exc_info=True)
    return None


def _date_to_iso(value) -> str | None:
    """Best-effort ISO-8601 date string from a date/datetime/Timestamp/str."""
    try:
        value = _nan_to_none(value)
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API (fetching)
# ---------------------------------------------------------------------------


def get_ticker_news(symbol: str, count: int = 10) -> dict | None:
    """Latest news headlines for *symbol*, or None on hard failure.

    No news is a valid answer: returns ``{"symbol", "count": 0, "news": []}``
    rather than None when the feed is simply empty.
    """
    try:
        t = market_data.ticker(symbol)
        try:
            items = t.news or []
        except Exception:
            logger.warning("news fetch failed for %s", symbol, exc_info=True)
            items = []

        news = []
        for item in items:
            parsed = parse_news_item(item)
            if parsed is not None:
                news.append(parsed)
            if len(news) >= count:
                break

        return {"symbol": symbol, "count": len(news), "news": news}
    except Exception:
        logger.exception("get_ticker_news failed for %s", symbol)
        return None


def get_earnings_info(symbol: str) -> dict | None:
    """Next earnings date, estimates, and recent surprises for *symbol*.

    Any missing piece degrades to None/[] -- returns None only when every
    source failed.
    """
    try:
        t = market_data.ticker(symbol)

        next_date = None
        try:
            next_date = _calendar_next_earnings(t.calendar)
        except Exception:
            logger.debug("calendar fetch failed for %s", symbol, exc_info=True)

        earnings = {"next_earnings": None, "recent": []}
        try:
            earnings = parse_earnings_dates(t.earnings_dates, pd.Timestamp.now())
        except Exception:
            logger.debug("earnings_dates fetch failed for %s", symbol, exc_info=True)

        info = {}
        try:
            info = t.info or {}
        except Exception:
            logger.debug("info fetch failed for %s", symbol, exc_info=True)

        next_from_dates = earnings.get("next_earnings") or {}
        result = {
            "symbol": symbol,
            "next_earnings_date": next_date or next_from_dates.get("date"),
            "next_eps_estimate": next_from_dates.get("eps_estimate"),
            "recent_earnings": earnings.get("recent", []),
            "trailing_eps": safe_round(info.get("trailingEps")),
            "forward_eps": safe_round(info.get("forwardEps")),
            "earnings_growth": safe_round(info.get("earningsGrowth")),
            "revenue_growth": safe_round(info.get("revenueGrowth")),
        }

        # Only a total blank counts as failure.
        if (result["next_earnings_date"] is None and not result["recent_earnings"]
                and result["trailing_eps"] is None and result["forward_eps"] is None):
            return None
        return result
    except Exception:
        logger.exception("get_earnings_info failed for %s", symbol)
        return None


def get_analyst_ratings(symbol: str) -> dict | None:
    """Analyst price targets and recommendation trend for *symbol*."""
    try:
        t = market_data.ticker(symbol)

        info = {}
        try:
            info = t.info or {}
        except Exception:
            logger.debug("info fetch failed for %s", symbol, exc_info=True)

        trend = []
        for attr in ("recommendations_summary", "recommendations"):
            try:
                trend = parse_recommendation_trend(getattr(t, attr))
            except Exception:
                logger.debug("%s fetch failed for %s", attr, symbol, exc_info=True)
                trend = []
            if trend:
                break

        if not info and not trend:
            return None

        current_price = info.get("currentPrice") or info.get("previousClose")
        target_mean = info.get("targetMeanPrice")
        upside_pct = (
            safe_round(target_mean / current_price - 1)
            if target_mean and current_price else None
        )

        return {
            "symbol": symbol,
            "current_price": safe_round(current_price),
            "target_mean": safe_round(target_mean),
            "target_high": safe_round(info.get("targetHighPrice")),
            "target_low": safe_round(info.get("targetLowPrice")),
            "target_median": safe_round(info.get("targetMedianPrice")),
            "upside_pct": upside_pct,
            "recommendation": info.get("recommendationKey"),
            "recommendation_mean": safe_round(info.get("recommendationMean")),
            "recommendation_scale": "1 = strong buy ... 5 = sell",
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "trend": trend,
        }
    except Exception:
        logger.exception("get_analyst_ratings failed for %s", symbol)
        return None


def _sentiment_hint(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio > 1.2:
        tilt = "bearish tilt"
    elif ratio < 0.7:
        tilt = "bullish tilt"
    else:
        tilt = "neutral"
    return f"put/call volume {ratio:.2f} — {tilt}"


def get_options_snapshot(symbol: str, expiry: str = "") -> dict | None:
    """Options metrics for one expiry: put/call ratios, ATM IV, max pain."""
    try:
        t = market_data.ticker(symbol)
        expiries = list(t.options or ())
        if not expiries:
            return None

        chosen = expiry if expiry in expiries else expiries[0]
        chain = t.option_chain(chosen)

        spot = None
        try:
            info = t.info or {}
            spot = info.get("currentPrice") or info.get("previousClose")
            spot = float(spot) if spot is not None else None
        except Exception:
            logger.debug("spot lookup failed for %s", symbol, exc_info=True)

        metrics = compute_options_metrics(chain.calls, chain.puts, spot)

        return {
            "symbol": symbol,
            "expiry": chosen,
            "available_expiries": expiries[:6],
            "spot": safe_round(spot),
            **metrics,
            "sentiment_hint": _sentiment_hint(metrics["put_call_volume_ratio"]),
        }
    except Exception:
        logger.exception("get_options_snapshot failed for %s", symbol)
        return None
