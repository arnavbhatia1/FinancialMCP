"""yfinance wrapper for market data retrieval.

Every public function catches exceptions internally and returns None or an
empty container — callers never need to handle yfinance errors.
"""

import logging
from statistics import median

import yfinance as yf

from .utils import TRADING_DAYS_PER_YEAR, safe_round

logger = logging.getLogger(__name__)

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
        info = yf.Ticker(symbol).info
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
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        price = info.get("currentPrice")
        if price is not None:
            return float(price)

        hist = ticker.history(period="1d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
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
        hist = yf.Ticker(symbol).history(period="6mo")
        if hist.empty or len(hist) < 30:
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
                spy_hist = yf.Ticker("SPY").history(period="6mo")
                if not spy_hist.empty and len(spy_hist) >= 90:
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


