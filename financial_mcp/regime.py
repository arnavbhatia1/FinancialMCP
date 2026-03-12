"""Market regime detection using SPY and VIX data from yfinance.

Classifies the current market environment as one of:
BULL, BEAR, SIDEWAYS, HIGH_VOLATILITY, or CRASH.

Every public function catches exceptions internally and returns None or an
empty container -- callers never need to handle yfinance errors.
"""

import logging
from datetime import datetime, timedelta

import yfinance as yf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regime labels
# ---------------------------------------------------------------------------

REGIME_BULL = "BULL"
REGIME_BEAR = "BEAR"
REGIME_SIDEWAYS = "SIDEWAYS"
REGIME_HIGH_VOLATILITY = "HIGH_VOLATILITY"
REGIME_CRASH = "CRASH"

# ---------------------------------------------------------------------------
# Recommendations per regime
# ---------------------------------------------------------------------------

_RECOMMENDATIONS: dict[str, str] = {
    REGIME_BULL: (
        "Maintain equity exposure with a growth tilt. "
        "Consider trailing stops to protect gains."
    ),
    REGIME_SIDEWAYS: (
        "Favor income-generating strategies and sector rotation. "
        "Keep position sizes moderate."
    ),
    REGIME_HIGH_VOLATILITY: (
        "Favor defensive positions and increase cash allocation. "
        "Reduce leverage and tighten stop-losses."
    ),
    REGIME_BEAR: (
        "Prioritize capital preservation. Raise cash, favor defensive sectors, "
        "and consider hedging with inverse ETFs or put options."
    ),
    REGIME_CRASH: (
        "Extreme risk-off environment. Maximize cash, halt new equity purchases, "
        "and review all stop-losses immediately."
    ),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_round(value: float | None, decimals: int = 4) -> float | None:
    """Round *value* if it is not None."""
    if value is None:
        return None
    return round(float(value), decimals)


def _classify_regime(score: int, crash_signal: int) -> str:
    """Map a composite score to a regime label.

    If the crash signal was triggered (<= -3), the regime is always CRASH
    regardless of the total score.
    """
    if crash_signal <= -3:
        return REGIME_CRASH
    if score >= 3:
        return REGIME_BULL
    if score >= 1:
        return REGIME_SIDEWAYS
    if score >= -1:
        return REGIME_SIDEWAYS
    if score >= -3:
        return REGIME_HIGH_VOLATILITY
    return REGIME_BEAR


def _compute_signals(spy_close, vix_close) -> dict | None:
    """Compute all regime signals from SPY and VIX close-price Series.

    Returns a dict with keys: trend, momentum, volatility, breadth,
    crash_signal, and a details sub-dict, or None if the data is
    insufficient.
    """
    if spy_close is None or len(spy_close) < 200:
        logger.warning(
            "Insufficient SPY data for regime detection: need 200 days, got %s",
            len(spy_close) if spy_close is not None else 0,
        )
        return None

    if vix_close is None or vix_close.empty:
        logger.warning("VIX data is empty; cannot compute volatility signal")
        return None

    current_price = float(spy_close.iloc[-1])
    vix_current = float(vix_close.iloc[-1])

    # a) Trend: 50-day SMA vs 200-day SMA -----------------------------------
    sma_50 = float(spy_close.rolling(window=50).mean().iloc[-1])
    sma_200 = float(spy_close.rolling(window=200).mean().iloc[-1])

    if sma_200 == 0:
        trend_signal = 0
    else:
        sma_ratio = sma_50 / sma_200 - 1
        if sma_ratio > 0.01:
            trend_signal = 1
        elif sma_ratio < -0.01:
            trend_signal = -1
        else:
            trend_signal = 0

    # b) Momentum: 20-day return ---------------------------------------------
    if len(spy_close) >= 20:
        price_20d_ago = float(spy_close.iloc[-20])
        if price_20d_ago != 0:
            return_20d = current_price / price_20d_ago - 1
        else:
            return_20d = 0.0
    else:
        return_20d = 0.0

    if return_20d > 0.05:
        momentum_signal = 2
    elif return_20d > 0:
        momentum_signal = 1
    elif return_20d < -0.05:
        momentum_signal = -2
    else:
        momentum_signal = -1

    # c) Volatility: VIX level -----------------------------------------------
    if vix_current < 15:
        volatility_signal = 1
        vix_level = "low"
    elif vix_current <= 25:
        volatility_signal = 0
        vix_level = "normal"
    elif vix_current <= 35:
        volatility_signal = -1
        vix_level = "high"
    else:
        volatility_signal = -2
        vix_level = "extreme"

    # d) Breadth proxy: distance from 52-week high ---------------------------
    high_52w = float(spy_close.iloc[-252:].max()) if len(spy_close) >= 252 else float(spy_close.max())
    if high_52w == 0:
        breadth_signal = 0
    else:
        distance = 1 - current_price / high_52w
        if distance <= 0.05:
            breadth_signal = 1
        elif distance <= 0.10:
            breadth_signal = 0
        elif distance <= 0.20:
            breadth_signal = -1
        else:
            breadth_signal = -2

    # e) Rate of decline: 5-day return (crash detection) ---------------------
    if len(spy_close) >= 5:
        price_5d_ago = float(spy_close.iloc[-5])
        return_5d = (current_price / price_5d_ago - 1) if price_5d_ago != 0 else 0.0
    else:
        return_5d = 0.0

    crash_signal = -3 if return_5d < -0.07 else 0

    return {
        "signals": {
            "trend": trend_signal,
            "momentum": momentum_signal,
            "volatility": volatility_signal,
            "breadth": breadth_signal,
            "crash_signal": crash_signal,
        },
        "details": {
            "spy_price": _safe_round(current_price),
            "spy_50sma": _safe_round(sma_50),
            "spy_200sma": _safe_round(sma_200),
            "spy_20d_return": _safe_round(return_20d),
            "spy_52w_high": _safe_round(high_52w),
            "spy_5d_return": _safe_round(return_5d),
            "vix": _safe_round(vix_current),
            "vix_level": vix_level,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_regime() -> dict | None:
    """Classify the current market regime using SPY and VIX data.

    Fetches 1 year of daily data (to have enough history for the 200-day SMA)
    and computes trend, momentum, volatility, breadth, and crash signals.

    Returns a dict with keys: regime, score, signals, details, recommendation.
    Returns None on any data-fetching or computation failure.
    """
    try:
        # Fetch SPY and VIX together for efficiency.
        end = datetime.now()
        start = end - timedelta(days=365)

        data = yf.download(
            ["SPY", "^VIX"],
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
        )

        if data.empty:
            logger.warning("yf.download returned empty data for SPY/VIX")
            return None

        # yf.download with multiple tickers returns multi-level columns.
        spy_close = data["Close"]["SPY"].dropna()
        vix_close = data["Close"]["^VIX"].dropna()

        result = _compute_signals(spy_close, vix_close)
        if result is None:
            return None

        signals = result["signals"]
        score = (
            signals["trend"]
            + signals["momentum"]
            + signals["volatility"]
            + signals["breadth"]
            + signals["crash_signal"]
        )
        regime = _classify_regime(score, signals["crash_signal"])

        return {
            "regime": regime,
            "score": score,
            "signals": signals,
            "details": result["details"],
            "recommendation": _RECOMMENDATIONS[regime],
        }
    except Exception:
        logger.exception("detect_regime failed")
        return None


def get_regime_history(months: int = 12) -> list[dict] | None:
    """Compute monthly regime classification for the last *months* months.

    For each month, uses the last trading day's available data to classify
    the regime. Requires enough history for the 200-day SMA, so fetches
    roughly months + 12 months of data.

    Returns a list of ``{month, regime, score}`` dicts, or None on failure.
    """
    try:
        # Need ~200 extra trading days for the SMA calculation.
        end = datetime.now()
        start = end - timedelta(days=(months + 12) * 31)

        data = yf.download(
            ["SPY", "^VIX"],
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
        )

        if data.empty:
            logger.warning("yf.download returned empty data for regime history")
            return None

        spy_close = data["Close"]["SPY"].dropna()
        vix_close = data["Close"]["^VIX"].dropna()

        if len(spy_close) < 200:
            logger.warning(
                "Insufficient SPY history for regime_history: %d days", len(spy_close)
            )
            return None

        # Determine which calendar months fall in the requested window.
        cutoff = end - timedelta(days=months * 31)
        results: list[dict] = []
        processed_months: set[str] = set()

        # Walk backwards through the SPY index to find the last trading day
        # of each month.
        for date in reversed(spy_close.index):
            month_key = date.strftime("%Y-%m")
            if month_key in processed_months:
                continue
            if date < cutoff:
                break

            # Slice data up to this date to simulate computing signals
            # as of month-end.
            spy_slice = spy_close.loc[:date]
            vix_slice = vix_close.loc[:date]

            if len(spy_slice) < 200:
                continue

            result = _compute_signals(spy_slice, vix_slice)
            if result is None:
                continue

            signals = result["signals"]
            score = (
                signals["trend"]
                + signals["momentum"]
                + signals["volatility"]
                + signals["breadth"]
                + signals["crash_signal"]
            )
            regime = _classify_regime(score, signals["crash_signal"])

            results.append({
                "month": month_key,
                "regime": regime,
                "score": score,
            })
            processed_months.add(month_key)

        # Return in chronological order.
        results.sort(key=lambda r: r["month"])
        return results
    except Exception:
        logger.exception("get_regime_history failed")
        return None


def get_vix_analysis() -> dict | None:
    """Analyse the VIX over the last year.

    Returns current level, 1-year statistics, a simple term-structure
    signal, and a qualitative fear/complacency label.
    Returns None on any data-fetching or computation failure.
    """
    try:
        end = datetime.now()
        start = end - timedelta(days=365)

        vix_data = yf.download(
            "^VIX",
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
        )

        if vix_data.empty:
            logger.warning("yf.download returned empty data for VIX analysis")
            return None

        vix_close = vix_data["Close"].dropna()
        if hasattr(vix_close, "columns"):
            # Single-ticker download may still produce a DataFrame column.
            vix_close = vix_close.squeeze()

        if vix_close.empty:
            return None

        current = float(vix_close.iloc[-1])
        high_1y = float(vix_close.max())
        low_1y = float(vix_close.min())
        mean_1y = float(vix_close.mean())

        # Percentile: fraction of past-year observations below the current level.
        percentile_1y = float((vix_close < current).sum() / len(vix_close))

        # Term structure proxy: compare current VIX to its 20-day average.
        # If current < 20d avg, the curve is in contango (normal); if above,
        # backwardation (fear premium).
        if len(vix_close) >= 20:
            vix_20d_avg = float(vix_close.rolling(window=20).mean().iloc[-1])
            term_structure = "contango" if current <= vix_20d_avg else "backwardation"
        else:
            term_structure = "contango"

        # Qualitative signal.
        if current < 12:
            signal = "complacent"
        elif current <= 20:
            signal = "normal"
        elif current <= 30:
            signal = "fearful"
        else:
            signal = "panic"

        return {
            "current": _safe_round(current),
            "percentile_1y": _safe_round(percentile_1y),
            "mean_1y": _safe_round(mean_1y),
            "high_1y": _safe_round(high_1y),
            "low_1y": _safe_round(low_1y),
            "term_structure": term_structure,
            "signal": signal,
        }
    except Exception:
        logger.exception("get_vix_analysis failed")
        return None
