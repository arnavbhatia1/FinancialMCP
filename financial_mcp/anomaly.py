"""Anomaly detection and market scanner built on yfinance.

Every public function catches exceptions internally and returns None or an
empty list — callers never need to handle yfinance errors.

Uses ``yf.download()`` with multi-ticker batching where possible.  The
returned DataFrame has MultiIndex columns of the form ``(field, symbol)``
when more than one ticker is requested.
"""

import logging
import math
from datetime import datetime, timedelta

import yfinance as yf

logger = logging.getLogger(__name__)

_DEFAULT_SYMBOLS: list[str] = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "V",
    "JNJ", "WMT", "PG", "HD", "MA", "UNH", "BAC", "XOM", "CVX", "ABBV",
    "PFE", "COST", "MRK", "AVGO", "KO", "PEP", "TMO", "LLY", "CSCO",
    "CRM", "ACN", "ABT", "MCD", "NKE", "DHR", "TXN", "QCOM", "INTC",
    "AMAT", "AMD", "ADBE", "NFLX", "DIS", "PYPL", "BA", "GS", "MS",
    "BLK", "C", "WFC", "SCHW",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_anomalies(
    symbols: list[str] | None = None,
    lookback_days: int = 30,
) -> list[dict]:
    """Scan *symbols* for anomalies over the last *lookback_days*.

    Returns a list of dicts sorted by ``total_score`` descending.  Each dict
    has keys ``symbol``, ``anomalies`` (list of anomaly dicts), and
    ``total_score``.  Only symbols with at least one anomaly are included.

    Each anomaly dict has keys ``type``, ``score``, ``description``, and
    ``value``.
    """
    try:
        symbols = symbols or _DEFAULT_SYMBOLS
        if not symbols:
            return []

        # Fetch enough history for 52-week high/low and volatility calcs.
        # 52 weeks ~ 365 days; add buffer for weekends/holidays.
        fetch_days = max(lookback_days, 400)
        end = datetime.now()
        start = end - timedelta(days=fetch_days)

        df = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            group_by="ticker",
        )

        if df is None or df.empty:
            logger.info("scan_anomalies: no data returned from yf.download")
            return []

        results: list[dict] = []
        single_ticker = len(symbols) == 1

        for symbol in symbols:
            try:
                ticker_df = _extract_ticker_df(df, symbol, single_ticker)
                if ticker_df is None or len(ticker_df) < 30:
                    continue

                anomalies = _detect_anomalies(ticker_df, symbol, lookback_days)
                if anomalies:
                    total_score = sum(a["score"] for a in anomalies)
                    results.append({
                        "symbol": symbol,
                        "anomalies": anomalies,
                        "total_score": total_score,
                    })
            except Exception:
                logger.exception(
                    "scan_anomalies: error processing %s", symbol
                )
                continue

        results.sort(key=lambda r: r["total_score"], reverse=True)
        return results
    except Exception:
        logger.exception("scan_anomalies failed")
        return []


def scan_volume_leaders(
    symbols: list[str] | None = None,
    min_ratio: float = 2.0,
) -> list[dict]:
    """Find symbols whose today's volume exceeds their 20-day average.

    If *symbols* is None, scans the default list of 50 major tickers.
    Returns only those with ``volume / avg_20d_volume >= min_ratio``,
    sorted by ratio descending.
    """
    try:
        symbols = symbols or _DEFAULT_SYMBOLS
        if not symbols:
            return []

        end = datetime.now()
        start = end - timedelta(days=40)  # ~20 trading days with buffer

        df = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            group_by="ticker",
        )

        if df is None or df.empty:
            logger.info("scan_volume_leaders: no data returned")
            return []

        results: list[dict] = []
        single_ticker = len(symbols) == 1

        for symbol in symbols:
            try:
                ticker_df = _extract_ticker_df(df, symbol, single_ticker)
                if ticker_df is None or len(ticker_df) < 5:
                    continue

                volumes = ticker_df["Volume"].dropna()
                if len(volumes) < 5:
                    continue

                volume_today = float(volumes.iloc[-1])
                volume_avg_20d = float(volumes.iloc[-21:-1].mean()) if len(volumes) >= 21 else float(volumes.iloc[:-1].mean())

                if volume_avg_20d <= 0:
                    continue

                ratio = volume_today / volume_avg_20d

                if ratio >= min_ratio:
                    closes = ticker_df["Close"].dropna()
                    price_change_pct = 0.0
                    if len(closes) >= 2:
                        price_change_pct = round(
                            (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100,
                            2,
                        )

                    results.append({
                        "symbol": symbol,
                        "volume_today": int(volume_today),
                        "volume_avg_20d": int(volume_avg_20d),
                        "ratio": round(ratio, 2),
                        "price_change_pct": price_change_pct,
                    })
            except Exception:
                logger.exception(
                    "scan_volume_leaders: error processing %s", symbol
                )
                continue

        results.sort(key=lambda r: r["ratio"], reverse=True)
        return results
    except Exception:
        logger.exception("scan_volume_leaders failed")
        return []


def scan_gap_movers(
    symbols: list[str] | None = None,
    min_gap_pct: float = 2.0,
) -> list[dict]:
    """Find symbols that gapped up or down at the open.

    If *symbols* is None, scans the default list of 50 major tickers.
    Returns only those with ``abs(gap) >= min_gap_pct``, sorted by
    absolute gap size descending.
    """
    try:
        symbols = symbols or _DEFAULT_SYMBOLS
        if not symbols:
            return []

        end = datetime.now()
        start = end - timedelta(days=10)  # need today + previous close

        df = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            group_by="ticker",
        )

        if df is None or df.empty:
            logger.info("scan_gap_movers: no data returned")
            return []

        results: list[dict] = []
        single_ticker = len(symbols) == 1

        for symbol in symbols:
            try:
                ticker_df = _extract_ticker_df(df, symbol, single_ticker)
                if ticker_df is None or len(ticker_df) < 2:
                    continue

                opens = ticker_df["Open"].dropna()
                closes = ticker_df["Close"].dropna()
                if len(opens) < 2 or len(closes) < 2:
                    continue

                today_open = float(opens.iloc[-1])
                prev_close = float(closes.iloc[-2])

                if prev_close <= 0:
                    continue

                gap_pct = ((today_open - prev_close) / prev_close) * 100.0

                if abs(gap_pct) >= min_gap_pct:
                    current_price = float(closes.iloc[-1])
                    results.append({
                        "symbol": symbol,
                        "gap_pct": round(gap_pct, 2),
                        "gap_percent": round(gap_pct, 2),
                        "direction": "up" if gap_pct > 0 else "down",
                        "open": round(today_open, 2),
                        "prev_close": round(prev_close, 2),
                        "current_price": round(current_price, 2),
                    })
            except Exception:
                logger.exception(
                    "scan_gap_movers: error processing %s", symbol
                )
                continue

        results.sort(key=lambda r: abs(r["gap_pct"]), reverse=True)
        return results
    except Exception:
        logger.exception("scan_gap_movers failed")
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_ticker_df(df, symbol: str, single_ticker: bool):
    """Extract a single-ticker DataFrame from a batch ``yf.download()`` result.

    When ``single_ticker`` is True the columns are flat (``Close``, ``Open``,
    etc.).  When False the columns are a MultiIndex like ``("Close", "AAPL")``.
    Returns None if the symbol is missing or the resulting frame is empty.
    """
    try:
        if single_ticker:
            # yfinance 1.2+ always returns MultiIndex even for single tickers.
            # Flatten it by extracting the symbol if the columns are multi-level.
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                if symbol in df.columns.get_level_values(0):
                    ticker_df = df[symbol].copy()
                elif symbol in df.columns.get_level_values(1):
                    ticker_df = df.xs(symbol, axis=1, level=1).copy()
                else:
                    ticker_df = df.droplevel(0, axis=1).copy()
            else:
                ticker_df = df.copy()
        else:
            if symbol not in df.columns.get_level_values(0):
                # Try level 1 (group_by="ticker" puts ticker at level 0,
                # but the actual layout depends on yfinance version).
                if symbol in df.columns.get_level_values(1):
                    ticker_df = df.xs(symbol, axis=1, level=1).copy()
                else:
                    return None
            else:
                ticker_df = df[symbol].copy()

        ticker_df = ticker_df.dropna(how="all")
        if ticker_df.empty:
            return None
        return ticker_df
    except Exception:
        logger.debug("_extract_ticker_df: failed for %s", symbol)
        return None


def _compute_rsi(closes, period: int = 14) -> float | None:
    """Compute RSI using exponential moving average of gains and losses.

    *closes* should be a pandas Series of closing prices.  Returns a float
    in the range 0-100, or None if there is insufficient data.
    """
    try:
        if closes is None or len(closes) < period + 1:
            return None

        deltas = closes.diff().dropna()
        if len(deltas) < period:
            return None

        gains = deltas.clip(lower=0)
        losses = (-deltas.clip(upper=0))

        # Seed with simple average for the first window, then EMA thereafter.
        avg_gain = float(gains.iloc[:period].mean())
        avg_loss = float(losses.iloc[:period].mean())

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + float(gains.iloc[i])) / period
            avg_loss = (avg_loss * (period - 1) + float(losses.iloc[i])) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(rsi, 2)
    except Exception:
        logger.debug("_compute_rsi failed")
        return None


def _detect_anomalies(
    ticker_df,
    symbol: str,
    lookback_days: int,
) -> list[dict]:
    """Run all anomaly checks on a single ticker's DataFrame.

    Returns a list of anomaly dicts (may be empty).
    """
    anomalies: list[dict] = []

    closes = ticker_df["Close"].dropna()
    opens = ticker_df["Open"].dropna()
    highs = ticker_df["High"].dropna()
    lows = ticker_df["Low"].dropna()
    volumes = ticker_df["Volume"].dropna()

    if len(closes) < 21:
        return anomalies

    # -- Volume spike ----------------------------------------------------------
    try:
        if len(volumes) >= 21:
            volume_today = float(volumes.iloc[-1])
            volume_avg_20d = float(volumes.iloc[-21:-1].mean())
            if volume_avg_20d > 0:
                volume_ratio = volume_today / volume_avg_20d
                if volume_ratio > 3.0:
                    anomalies.append({
                        "type": "volume_spike",
                        "score": min(10, int(volume_ratio)),
                        "description": (
                            f"{symbol} volume {volume_ratio:.1f}x the 20-day average"
                        ),
                        "value": round(volume_ratio, 2),
                    })
    except Exception:
        logger.debug("volume spike check failed for %s", symbol)

    # -- Price gap -------------------------------------------------------------
    try:
        if len(opens) >= 2 and len(closes) >= 2:
            today_open = float(opens.iloc[-1])
            prev_close = float(closes.iloc[-2])
            if prev_close > 0:
                gap_pct = ((today_open - prev_close) / prev_close) * 100.0
                if abs(gap_pct) > 3.0:
                    direction = "up" if gap_pct > 0 else "down"
                    anomalies.append({
                        "type": "price_gap",
                        "score": min(10, int(abs(gap_pct) * 2)),
                        "description": (
                            f"{symbol} gapped {direction} {abs(gap_pct):.1f}% "
                            f"at open"
                        ),
                        "value": round(gap_pct, 2),
                    })
    except Exception:
        logger.debug("price gap check failed for %s", symbol)

    # -- Unusual range ---------------------------------------------------------
    try:
        if len(highs) >= 21 and len(lows) >= 21:
            ranges = highs - lows
            today_range = float(ranges.iloc[-1])
            avg_range_20d = float(ranges.iloc[-21:-1].mean())
            if avg_range_20d > 0:
                range_ratio = today_range / avg_range_20d
                if range_ratio > 2.0:
                    anomalies.append({
                        "type": "unusual_range",
                        "score": min(10, int(range_ratio * 2)),
                        "description": (
                            f"{symbol} intraday range {range_ratio:.1f}x "
                            f"the 20-day average"
                        ),
                        "value": round(range_ratio, 2),
                    })
    except Exception:
        logger.debug("unusual range check failed for %s", symbol)

    # -- New 52-week high/low --------------------------------------------------
    try:
        if len(closes) >= 252:
            high_52w = float(closes.iloc[-252:].max())
            low_52w = float(closes.iloc[-252:].min())
            current = float(closes.iloc[-1])

            if high_52w > 0 and current >= high_52w * 0.99:
                anomalies.append({
                    "type": "52_week_high",
                    "score": 7,
                    "description": (
                        f"{symbol} at or within 1% of 52-week high "
                        f"(${high_52w:.2f})"
                    ),
                    "value": round(current, 2),
                })

            if low_52w > 0 and current <= low_52w * 1.01:
                anomalies.append({
                    "type": "52_week_low",
                    "score": 8,
                    "description": (
                        f"{symbol} at or within 1% of 52-week low "
                        f"(${low_52w:.2f})"
                    ),
                    "value": round(current, 2),
                })
    except Exception:
        logger.debug("52-week high/low check failed for %s", symbol)

    # -- Volatility expansion --------------------------------------------------
    try:
        daily_returns = closes.pct_change().dropna()
        if len(daily_returns) >= 30:
            vol_5d = float(daily_returns.iloc[-5:].std())
            vol_30d = float(daily_returns.iloc[-30:].std())
            if vol_30d > 0:
                vol_ratio = vol_5d / vol_30d
                if vol_ratio > 2.0:
                    anomalies.append({
                        "type": "volatility_expansion",
                        "score": min(10, int(vol_ratio * 2)),
                        "description": (
                            f"{symbol} 5-day volatility {vol_ratio:.1f}x "
                            f"the 30-day volatility"
                        ),
                        "value": round(vol_ratio, 2),
                    })
    except Exception:
        logger.debug("volatility expansion check failed for %s", symbol)

    # -- Momentum divergence ---------------------------------------------------
    try:
        if len(closes) >= 20:
            rsi = _compute_rsi(closes, period=14)
            if rsi is not None:
                high_20d = float(closes.iloc[-20:].max())
                low_20d = float(closes.iloc[-20:].min())
                current = float(closes.iloc[-1])

                # Bearish divergence: new 20-day high but RSI < 50.
                if math.isclose(current, high_20d, rel_tol=1e-9) and rsi < 50:
                    anomalies.append({
                        "type": "momentum_divergence",
                        "score": 6,
                        "description": (
                            f"{symbol} at 20-day high but RSI is {rsi:.1f} "
                            f"(bearish divergence)"
                        ),
                        "value": rsi,
                    })

                # Bullish divergence: new 20-day low but RSI > 50.
                if math.isclose(current, low_20d, rel_tol=1e-9) and rsi > 50:
                    anomalies.append({
                        "type": "momentum_divergence",
                        "score": 6,
                        "description": (
                            f"{symbol} at 20-day low but RSI is {rsi:.1f} "
                            f"(bullish divergence)"
                        ),
                        "value": rsi,
                    })
    except Exception:
        logger.debug("momentum divergence check failed for %s", symbol)

    return anomalies
