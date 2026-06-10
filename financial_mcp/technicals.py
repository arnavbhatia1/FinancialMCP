"""Technical analysis layer for the FinancialMCP package.

Pure computation functions (``compute_indicators``, ``compute_returns``) are
separated from data fetching so they are unit-testable offline.

Every public function catches exceptions internally and returns None or an
empty container -- callers never need to handle errors.
"""

import logging

import pandas as pd

from . import market_data
from .utils import TRADING_DAYS_PER_YEAR, safe_round

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}
VALID_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo"}

_MAX_BARS = 250

# The 11 SPDR sector ETFs.
SECTOR_ETFS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLU": "Utilities",
    "XLC": "Communication Services",
}

_BENCHMARK = "SPY"

_EMPTY_RETURNS = {
    "return_1d": None,
    "return_5d": None,
    "return_1mo": None,
    "return_3mo": None,
}


# ---------------------------------------------------------------------------
# Pure computation helpers (no network I/O)
# ---------------------------------------------------------------------------


def _wilder_rsi(close: pd.Series, window: int = 14) -> float:
    """RSI with Wilder's smoothing (ewm alpha=1/window)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = float(gain.ewm(alpha=1 / window, adjust=False).mean().iloc[-1])
    avg_loss = float(loss.ewm(alpha=1 / window, adjust=False).mean().iloc[-1])
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _rsi_label(rsi: float) -> str:
    if rsi < 30:
        return "oversold"
    if rsi > 70:
        return "overbought"
    return "neutral"


def compute_indicators(hist: pd.DataFrame) -> dict:
    """Compute all technical indicators from an OHLCV DataFrame.

    Pure function -- no network I/O. Expects columns Open/High/Low/Close/Volume
    with a DatetimeIndex.
    """
    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]
    price = float(close.iloc[-1])
    n = len(close)

    # -- RSI 14 (Wilder's smoothing) ------------------------------------------
    rsi = _wilder_rsi(close, 14)

    # -- MACD 12/26/9 ----------------------------------------------------------
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd = float(macd_line.iloc[-1])
    signal = float(signal_line.iloc[-1])
    histogram = macd - signal
    histogram_prev = (
        float(macd_line.iloc[-2] - signal_line.iloc[-2]) if n >= 2 else histogram
    )
    crossover = "bullish" if macd > signal else "bearish"

    # -- SMAs 20/50/200 ----------------------------------------------------------
    sma_20 = float(close.rolling(window=20).mean().iloc[-1]) if n >= 20 else None
    sma_50 = float(close.rolling(window=50).mean().iloc[-1]) if n >= 50 else None
    sma_200 = float(close.rolling(window=200).mean().iloc[-1]) if n >= 200 else None

    price_above_sma20 = (price > sma_20) if sma_20 is not None else None
    price_above_sma50 = (price > sma_50) if sma_50 is not None else None
    price_above_sma200 = (price > sma_200) if sma_200 is not None else None
    golden_cross = (
        (sma_50 > sma_200) if (sma_50 is not None and sma_200 is not None) else None
    )

    # -- Bollinger Bands 20/2 ------------------------------------------------------
    bb_upper = bb_lower = bb_middle = percent_b = bandwidth = None
    if n >= 20:
        bb_middle = sma_20
        std_20 = float(close.rolling(window=20).std().iloc[-1])
        bb_upper = bb_middle + 2 * std_20
        bb_lower = bb_middle - 2 * std_20
        band_range = bb_upper - bb_lower
        # Constant prices give zero bandwidth -- guard the division.
        percent_b = (price - bb_lower) / band_range if band_range != 0 else None
        bandwidth = band_range / bb_middle if bb_middle != 0 else None

    # -- ATR 14 (Wilder's smoothing on true range) ----------------------------------
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = float(true_range.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    atr_pct = atr / price if price != 0 else None

    # -- 52-week range (over available window, max 252 rows) --------------------------
    window_52w = close.iloc[-TRADING_DAYS_PER_YEAR:]
    high_52w = float(window_52w.max())
    low_52w = float(window_52w.min())
    pct_from_52w_high = (price / high_52w - 1) if high_52w != 0 else None
    pct_from_52w_low = (price / low_52w - 1) if low_52w != 0 else None

    # -- Volume ------------------------------------------------------------------------
    avg_volume_20d = volume_ratio = None
    if n >= 20:
        avg_volume_20d = float(volume.rolling(window=20).mean().iloc[-1])
        volume_ratio = (
            float(volume.iloc[-1]) / avg_volume_20d if avg_volume_20d else None
        )

    indicators = {
        "rsi_14": safe_round(rsi, 2),
        "rsi_label": _rsi_label(rsi),
        "macd": {
            "macd": safe_round(macd),
            "signal": safe_round(signal),
            "histogram": safe_round(histogram),
            "crossover": crossover,
        },
        "sma_20": safe_round(sma_20),
        "sma_50": safe_round(sma_50),
        "sma_200": safe_round(sma_200),
        "price_above_sma20": price_above_sma20,
        "price_above_sma50": price_above_sma50,
        "price_above_sma200": price_above_sma200,
        "golden_cross": golden_cross,
        "bollinger": {
            "upper": safe_round(bb_upper),
            "lower": safe_round(bb_lower),
            "middle": safe_round(bb_middle),
            "percent_b": safe_round(percent_b),
            "bandwidth": safe_round(bandwidth),
        },
        "atr_14": safe_round(atr),
        "atr_pct": safe_round(atr_pct),
        "high_52w": safe_round(high_52w),
        "low_52w": safe_round(low_52w),
        "pct_from_52w_high": safe_round(pct_from_52w_high),
        "pct_from_52w_low": safe_round(pct_from_52w_low),
        "avg_volume_20d": int(avg_volume_20d) if avg_volume_20d is not None else None,
        "volume_ratio": safe_round(volume_ratio, 2),
    }

    indicators["summary"] = _build_summary(
        rsi=rsi,
        crossover=crossover,
        histogram=histogram,
        histogram_prev=histogram_prev,
        price_above_sma20=price_above_sma20,
        price_above_sma50=price_above_sma50,
        price_above_sma200=price_above_sma200,
        golden_cross=golden_cross,
        percent_b=percent_b,
        pct_from_52w_high=pct_from_52w_high,
        pct_from_52w_low=pct_from_52w_low,
        volume_ratio=volume_ratio,
    )
    return indicators


def _build_summary(
    *,
    rsi,
    crossover,
    histogram,
    histogram_prev,
    price_above_sma20,
    price_above_sma50,
    price_above_sma200,
    golden_cross,
    percent_b,
    pct_from_52w_high,
    pct_from_52w_low,
    volume_ratio,
) -> list[str]:
    """Short plain-English signal lines an LLM can read at a glance."""
    lines: list[str] = []

    if rsi is not None:
        lines.append(f"RSI {rsi:.1f} — {_rsi_label(rsi)}")

    direction = "rising" if histogram > histogram_prev else "falling"
    lines.append(f"MACD {crossover} (histogram {direction})")

    sma_flags = {
        "20": price_above_sma20,
        "50": price_above_sma50,
        "200": price_above_sma200,
    }
    known = {k: v for k, v in sma_flags.items() if v is not None}
    if known:
        above = [k for k, v in known.items() if v]
        below = [k for k, v in known.items() if not v]
        if not below:
            suffix = " — established uptrend" if "200" in above else ""
            lines.append(f"price above {'/'.join(above)}-day SMAs{suffix}")
        elif not above:
            suffix = " — established downtrend" if "200" in below else ""
            lines.append(f"price below {'/'.join(below)}-day SMAs{suffix}")
        else:
            lines.append(
                f"price above {'/'.join(above)}-day SMA"
                f"{'s' if len(above) > 1 else ''}, "
                f"below {'/'.join(below)}-day SMA{'s' if len(below) > 1 else ''}"
            )

    if golden_cross is True:
        lines.append("golden cross — 50-day SMA above 200-day SMA")
    elif golden_cross is False:
        lines.append("death cross — 50-day SMA below 200-day SMA")

    if percent_b is not None:
        if percent_b > 1:
            lines.append("price above the upper Bollinger band — stretched")
        elif percent_b < 0:
            lines.append("price below the lower Bollinger band — stretched")

    if pct_from_52w_high is not None:
        if pct_from_52w_high >= 0:
            lines.append("at a 52-week high")
        else:
            lines.append(f"{abs(pct_from_52w_high) * 100:.1f}% below 52-week high")
    if pct_from_52w_low is not None and pct_from_52w_low > 0:
        lines.append(f"{pct_from_52w_low * 100:.1f}% above 52-week low")

    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            lines.append(f"volume {volume_ratio:.1f}x the 20-day average")
        elif volume_ratio <= 0.5:
            lines.append(
                f"volume {volume_ratio:.1f}x the 20-day average — unusually light"
            )

    return lines


def compute_returns(close_series: pd.Series) -> dict:
    """Trailing returns from a Close-price Series.

    Pure function -- no network I/O. Returns 1d, 5d, 1mo (21 trading days)
    and 3mo (63 trading days) returns; None when the series is too short.
    """
    series = close_series.dropna()

    def _ret(lookback: int) -> float | None:
        # A price *lookback* bars ago needs lookback + 1 observations.
        if len(series) <= lookback:
            return None
        past = float(series.iloc[-(lookback + 1)])
        if past == 0:
            return None
        return float(series.iloc[-1]) / past - 1

    return {
        "return_1d": safe_round(_ret(1)),
        "return_5d": safe_round(_ret(5)),
        "return_1mo": safe_round(_ret(21)),
        "return_3mo": safe_round(_ret(63)),
    }


# ---------------------------------------------------------------------------
# Public API (fetching)
# ---------------------------------------------------------------------------


def get_price_history(
    symbol: str, period: str = "3mo", interval: str = "1d"
) -> dict | None:
    """OHLCV bars for *symbol*, or None on failure.

    Invalid period/interval returns an ``{"error": ...}`` dict describing the
    valid values so the calling agent learns the contract.
    """
    try:
        if period not in VALID_PERIODS:
            return {
                "error": (
                    f"Invalid period '{period}'. Valid periods: "
                    f"{', '.join(sorted(VALID_PERIODS))}"
                )
            }
        if interval not in VALID_INTERVALS:
            return {
                "error": (
                    f"Invalid interval '{interval}'. Valid intervals: "
                    f"{', '.join(sorted(VALID_INTERVALS))}"
                )
            }

        hist = market_data.get_history(symbol, period, interval)
        if hist is None or hist.empty:
            return None

        total_rows = len(hist)
        truncated = total_rows > _MAX_BARS
        window = hist.iloc[-_MAX_BARS:]

        bars = [
            {
                "date": idx.isoformat(),
                "open": safe_round(row["Open"]),
                "high": safe_round(row["High"]),
                "low": safe_round(row["Low"]),
                "close": safe_round(row["Close"]),
                "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else None,
            }
            for idx, row in window.iterrows()
        ]

        result = {
            "symbol": symbol,
            "period": period,
            "interval": interval,
            "count": len(bars),
            "bars": bars,
            "as_of": window.index[-1].isoformat(),
        }
        if truncated:
            result["truncated"] = True
            result["total_rows"] = total_rows
        return result
    except Exception:
        logger.exception("get_price_history failed for %s", symbol)
        return None


def get_technical_indicators(symbol: str) -> dict | None:
    """Full technical-indicator snapshot for *symbol*, or None on failure."""
    try:
        hist = market_data.get_history(symbol, "1y", "1d")
        if hist is None or hist.empty or len(hist) < 50:
            logger.warning(
                "Insufficient history for %s: need 50 rows, got %s",
                symbol,
                0 if hist is None else len(hist),
            )
            return None

        indicators = compute_indicators(hist)
        summary = indicators.pop("summary")

        return {
            "symbol": symbol,
            "price": safe_round(float(hist["Close"].iloc[-1])),
            "as_of": hist.index[-1].isoformat(),
            "indicators": indicators,
            "summary": summary,
        }
    except Exception:
        logger.exception("get_technical_indicators failed for %s", symbol)
        return None


def get_sector_performance() -> dict | None:
    """Performance of the 11 SPDR sector ETFs vs SPY, or None on failure."""
    try:
        symbols = list(SECTOR_ETFS) + [_BENCHMARK]
        df = market_data.yf_download(
            symbols, period="6mo", progress=False, group_by="column"
        )
        if df is None or df.empty or "Close" not in df.columns:
            logger.warning("Empty sector-ETF download")
            return None

        close = df["Close"]

        def _series(sym: str):
            if hasattr(close, "columns"):
                return close[sym] if sym in close.columns else None
            return close  # single-symbol degenerate case

        spy_close = _series(_BENCHMARK)
        benchmark = {"symbol": _BENCHMARK}
        benchmark.update(
            compute_returns(spy_close) if spy_close is not None else dict(_EMPTY_RETURNS)
        )

        sectors = []
        for sym, name in SECTOR_ETFS.items():
            series = _series(sym)
            returns = (
                compute_returns(series) if series is not None else dict(_EMPTY_RETURNS)
            )
            sectors.append({"symbol": sym, "name": name, **returns})

        # Sort by 1-month return descending; None sorts last.
        sectors.sort(key=lambda s: (s["return_1mo"] is None, -(s["return_1mo"] or 0)))
        ranked = [s for s in sectors if s["return_1mo"] is not None]

        return {
            "as_of": df.index[-1].isoformat(),
            "benchmark": benchmark,
            "sectors": sectors,
            "leaders": [{"symbol": s["symbol"], "name": s["name"]} for s in ranked[:3]],
            "laggards": [{"symbol": s["symbol"], "name": s["name"]} for s in ranked[-3:]],
        }
    except Exception:
        logger.exception("get_sector_performance failed")
        return None
