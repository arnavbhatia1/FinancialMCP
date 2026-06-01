"""yfinance wrapper for market data retrieval.

Every public function catches exceptions internally and returns None or an
empty container — callers never need to handle yfinance errors.
"""

import logging
import threading
import time
from statistics import median

import yfinance as yf

from .utils import TRADING_DAYS_PER_YEAR, safe_round

logger = logging.getLogger(__name__)

# ── Browser-impersonating session ─────────────────────────────────────────────
# Yahoo blocks requests whose TLS fingerprint looks like a bot/datacenter client,
# which is why yfinance works on a laptop but 429s on Streamlit Cloud / AWS / GCP.
# A curl_cffi session impersonating Chrome presents a real browser fingerprint,
# which is the standard remedy for cloud-host rate-limiting.
def _make_session():
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except Exception as e:  # pragma: no cover - depends on optional dep
        logger.warning(
            "curl_cffi session unavailable (%s); yfinance will use its default "
            "session and may be rate-limited on cloud hosts", e
        )
        return None


_SESSION = _make_session()


def get_session():
    """The shared impersonating session (or None). For other modules' yf calls."""
    return _SESSION


def ticker(symbol: str):
    """Public: yf.Ticker using the impersonating session when available."""
    return _ticker(symbol)


def _ticker(symbol: str):
    """yf.Ticker using the impersonating session when available."""
    if _SESSION is not None:
        try:
            return yf.Ticker(symbol, session=_SESSION)
        except TypeError:
            pass
    return yf.Ticker(symbol)


def yf_download(*args, **kwargs):
    """yf.download routed through the impersonating session when available."""
    if _SESSION is not None and "session" not in kwargs:
        try:
            return yf.download(*args, session=_SESSION, **kwargs)
        except TypeError:
            pass
    return yf.download(*args, **kwargs)


# ── Tiny TTL cache ─────────────────────────────────────────────────────────────
# The bot rescans the same tickers every cycle and the UI re-queries on each
# interaction, so the same .info / history is fetched repeatedly. Caching for a
# short window slashes call volume — the main driver of rate-limiting.
_CACHE_TTL = 120  # seconds
_cache: dict = {}
_cache_lock = threading.Lock()


def _cached(key: str, producer):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < _CACHE_TTL:
            return hit[1]
    value = producer()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _info(symbol: str) -> dict:
    """Cached, session-backed Ticker.info (the most rate-limited endpoint)."""
    return _cached(f"info:{symbol}", lambda: (_ticker(symbol).info or {})) or {}


def _history(symbol: str, period: str):
    """Cached, session-backed price history."""
    return _cached(f"hist:{symbol}:{period}", lambda: _ticker(symbol).history(period=period))

_FUNDAMENTALS_FIELD_MAP = {
    "trailingPE": "pe_ratio",
    "enterpriseToEbitda": "ev_to_ebitda",
    "priceToBook": "price_to_book",
    "dividendYield": "dividend_yield",
    "marketCap": "market_cap",
    "sector": "sector",
    "industry": "industry",
}


def get_fundamentals(symbol: str) -> dict | None:
    """Return key fundamental ratios for *symbol*, or None on failure.

    Returns a dict with keys: pe_ratio, ev_to_ebitda, price_to_book,
    dividend_yield, market_cap, sector, industry.
    """
    try:
        info = _info(symbol)
        if not info:
            return None

        result = {
            out_key: info.get(yf_key)
            for yf_key, out_key in _FUNDAMENTALS_FIELD_MAP.items()
        }

        # If every value came back None the ticker is likely invalid.
        if all(v is None for v in result.values()):
            return None

        return result
    except Exception:
        logger.exception("get_fundamentals failed for %s", symbol)
        return None


def get_current_price(symbol: str) -> float | None:
    """Return the latest price for *symbol*, or None on failure.

    Tries ``info["currentPrice"]`` first, then falls back to the last
    closing price from 1-day history.
    """
    try:
        # History first — the chart endpoint stays reachable from cloud IPs even
        # when the .info/quote endpoint is rate-limited.
        hist = _history(symbol, "1d")
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])

        price = _info(symbol).get("currentPrice")
        return float(price) if price is not None else None
    except Exception:
        logger.exception("get_current_price failed for %s", symbol)
        return None


def get_momentum_signals(symbol: str) -> dict | None:
    """Return momentum / volatility metrics for *symbol*, or None on failure.

    Uses 6 months of daily history.  Returns a dict with keys:
    price_momentum_30d, price_momentum_90d, volatility,
    relative_strength, max_drawdown.
    """
    try:
        hist = _history(symbol, "6mo")
        if hist is None or hist.empty or len(hist) < 30:
            return None

        closes = hist["Close"]
        current = closes.iloc[-1]

        # -- momentum ----------------------------------------------------------
        price_30d_ago = closes.iloc[-30] if len(closes) >= 30 else None
        price_90d_ago = closes.iloc[-90] if len(closes) >= 90 else None

        momentum_30d = (
            (current / price_30d_ago - 1) if price_30d_ago is not None else None
        )
        momentum_90d = (
            (current / price_90d_ago - 1) if price_90d_ago is not None else None
        )

        # -- volatility (annualised 30-day rolling std of daily returns) -------
        daily_returns = closes.pct_change().dropna()
        rolling_std = daily_returns.rolling(window=30).std()
        volatility = (
            float(rolling_std.iloc[-1] * (TRADING_DAYS_PER_YEAR ** 0.5))
            if len(rolling_std) >= 30 and rolling_std.iloc[-1] is not None
            else None
        )

        # -- relative strength vs SPY -----------------------------------------
        relative_strength = None
        if momentum_90d is not None:
            try:
                spy_hist = _history("SPY", "6mo")
                if spy_hist is not None and not spy_hist.empty and len(spy_hist) >= 90:
                    spy_return = spy_hist["Close"].iloc[-1] / spy_hist["Close"].iloc[-90] - 1
                    if spy_return != 0:
                        relative_strength = momentum_90d / spy_return
            except Exception:
                logger.debug("SPY fetch failed; relative_strength will be None")

        # -- max drawdown ------------------------------------------------------
        running_max = closes.cummax()
        drawdowns = (closes - running_max) / running_max
        max_drawdown = float(drawdowns.min())

        return {
            "price_momentum_30d": safe_round(momentum_30d),
            "price_momentum_90d": safe_round(momentum_90d),
            "volatility": safe_round(volatility),
            "relative_strength": safe_round(relative_strength),
            "max_drawdown": safe_round(max_drawdown),
        }
    except Exception:
        logger.exception("get_momentum_signals failed for %s", symbol)
        return None


def get_batch_fundamentals(symbols: list[str]) -> dict[str, dict]:
    """Return fundamentals for each symbol in *symbols*.

    Symbols whose lookup returns None are silently omitted from the result.
    """
    results: dict[str, dict] = {}
    for symbol in symbols:
        try:
            data = get_fundamentals(symbol)
            if data is not None:
                results[symbol] = data
        except Exception:
            # get_fundamentals already logs; this is a defensive belt.
            logger.exception("get_batch_fundamentals: unexpected error for %s", symbol)
    return results


def get_sector_medians(batch_fundamentals: dict[str, dict]) -> dict[str, dict]:
    """Compute median PE and EV/EBITDA per sector from *batch_fundamentals*.

    Returns ``{sector: {"median_pe": ..., "median_ev_ebitda": ...}}``.
    Sectors with no valid data points for a metric will have that metric
    set to None.
    """
    sector_groups: dict[str, list[dict]] = {}
    for data in batch_fundamentals.values():
        sector = data.get("sector")
        if sector is None:
            continue
        sector_groups.setdefault(sector, []).append(data)

    result: dict[str, dict] = {}
    for sector, entries in sector_groups.items():
        pe_values = [e["pe_ratio"] for e in entries if e.get("pe_ratio") is not None]
        ev_values = [
            e["ev_to_ebitda"] for e in entries if e.get("ev_to_ebitda") is not None
        ]
        result[sector] = {
            "median_pe": safe_round(median(pe_values)) if pe_values else None,
            "median_ev_ebitda": safe_round(median(ev_values)) if ev_values else None,
        }
    return result


