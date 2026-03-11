"""Scoring engine for the FinancialMCP server.

3-signal composite (valuation + momentum + risk), with optional sentiment overlay.

Default weights (no sentiment):
    valuation = 0.40, momentum = 0.35, risk = 0.25

With sentiment enabled:
    sentiment = 0.25, then the remaining 0.75 is distributed proportionally:
    valuation = 0.30, momentum = 0.2625, risk = 0.1875
"""

import logging
import math

from . import market_data

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weight presets
# ---------------------------------------------------------------------------

_WEIGHTS_BASE = {"valuation": 0.40, "momentum": 0.35, "risk": 0.25}
_WEIGHTS_WITH_SENTIMENT = {
    "valuation": 0.30,
    "momentum": 0.2625,
    "risk": 0.1875,
    "sentiment": 0.25,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize(value: float, min_val: float, max_val: float) -> float:
    """Scale *value* into [0, 1], clamped at both ends."""
    if max_val == min_val:
        return 0.5
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))


def percentile_rank(value: float, all_values: list[float]) -> float:
    """Return the percentile rank (0-100) of *value* within *all_values*.

    Returns 50 when the reference list is empty (no context = neutral).
    """
    if not all_values:
        return 50.0
    count_below = sum(1 for v in all_values if v < value)
    count_equal = sum(1 for v in all_values if v == value)
    n = len(all_values)
    # Standard percentile-rank formula: (B + 0.5 * E) / N * 100
    return (count_below + 0.5 * count_equal) / n * 100.0


# ---------------------------------------------------------------------------
# Valuation composite
# ---------------------------------------------------------------------------

_VALUATION_SUBS = {
    "pe_ratio": 0.25,
    "ev_ebitda": 0.25,
    "price_to_book": 0.20,
    "dividend_yield": 0.15,
    "market_cap": 0.15,
}


def compute_valuation_composite(
    fundamentals: dict | None,
    sector_medians: dict | None,
) -> float | None:
    """Weighted valuation score (0-100) from up to 5 sub-signals.

    Returns ``None`` when every sub-signal is unavailable.
    """
    if not fundamentals:
        return None

    sector_medians = sector_medians or {}
    scores: dict[str, float] = {}

    # -- PE, EV/EBITDA, P/B: lower relative to sector median is better ------
    for key in ("pe_ratio", "ev_ebitda", "price_to_book"):
        value = fundamentals.get(key)
        median = sector_medians.get(key)
        if value is None or median is None or median == 0:
            continue
        ratio = value / median
        scores[key] = 100.0 * (1.0 - normalize(ratio, 0.5, 2.0))

    # -- Dividend yield: higher is better ------------------------------------
    div_yield = fundamentals.get("dividend_yield")
    if div_yield is not None:
        scores["dividend_yield"] = normalize(div_yield, 0.0, 0.06) * 100.0

    # -- Market cap: larger = more stable ------------------------------------
    market_cap = fundamentals.get("market_cap")
    if market_cap is not None and market_cap > 0:
        scores["market_cap"] = normalize(math.log10(market_cap), 9, 12) * 100.0

    if not scores:
        return None

    # Redistribute missing weights proportionally to the present signals.
    active_weight = sum(_VALUATION_SUBS[k] for k in scores)
    composite = sum(
        (_VALUATION_SUBS[k] / active_weight) * scores[k] for k in scores
    )
    return composite


# ---------------------------------------------------------------------------
# Momentum composite
# ---------------------------------------------------------------------------

_MOMENTUM_SUBS = {
    "momentum_30d": 0.30,
    "momentum_90d": 0.30,
    "relative_strength": 0.25,
    "volatility": 0.15,
}


def compute_momentum_composite(
    momentum: dict | None,
    all_momentum: list[dict],
) -> float | None:
    """Weighted momentum score (0-100).

    Returns ``None`` when *momentum* is ``None``.
    """
    if momentum is None:
        return None

    scores: dict[str, float] = {}

    for key in ("momentum_30d", "momentum_90d", "relative_strength"):
        value = momentum.get(key)
        if value is None:
            continue
        ref = [m.get(key) for m in all_momentum if m.get(key) is not None]
        scores[key] = percentile_rank(value, ref)

    vol = momentum.get("volatility")
    if vol is not None:
        ref = [m.get("volatility") for m in all_momentum if m.get("volatility") is not None]
        scores["volatility"] = 100.0 - percentile_rank(vol, ref)

    if not scores:
        return None

    active_weight = sum(_MOMENTUM_SUBS[k] for k in scores)
    composite = sum(
        (_MOMENTUM_SUBS[k] / active_weight) * scores[k] for k in scores
    )
    return composite


# ---------------------------------------------------------------------------
# Risk penalty
# ---------------------------------------------------------------------------


def compute_risk_penalty(
    symbol: str,
    holdings: list[dict] | None,
    portfolio_value: float,
    config: dict | None,
) -> float:
    """Portfolio-aware risk penalty (0-100).

    Returns 0 when there is no portfolio context (holdings is ``None`` or empty).
    """
    if not holdings:
        return 0.0

    config = config or {}
    risk_cfg = config.get("risk", {})

    penalties: dict[str, float] = {}

    # -- Sector concentration (weight 0.40) ----------------------------------
    max_sector = risk_cfg.get("max_sector_pct", 0.30)
    symbol_sector = _get_sector_for_symbol(symbol, holdings)
    if symbol_sector and portfolio_value > 0:
        sector_value = sum(
            h.get("value", 0) for h in holdings if h.get("sector") == symbol_sector
        )
        sector_pct = sector_value / portfolio_value
        if sector_pct > max_sector:
            overage = (sector_pct - max_sector) / max_sector
            penalties["sector"] = min(overage * 100.0, 100.0)
        else:
            penalties["sector"] = 0.0

    # -- Geographic concentration (weight 0.35) ------------------------------
    geo_targets = risk_cfg.get("geo_targets", {})
    symbol_geo = _get_geo_for_symbol(symbol, holdings)
    if symbol_geo and geo_targets and portfolio_value > 0:
        max_geo = geo_targets.get(symbol_geo, 0.50)
        geo_value = sum(
            h.get("value", 0) for h in holdings if h.get("geo") == symbol_geo
        )
        geo_pct = geo_value / portfolio_value
        if geo_pct > max_geo:
            overage = (geo_pct - max_geo) / max_geo
            penalties["geo"] = min(overage * 100.0, 100.0)
        else:
            penalties["geo"] = 0.0

    # -- Max drawdown (weight 0.25) ------------------------------------------
    drawdown = risk_cfg.get("max_drawdown", {}).get(symbol)
    if drawdown is None:
        # Try to find drawdown in the holdings entry for this symbol.
        for h in holdings:
            if h.get("symbol") == symbol:
                drawdown = h.get("max_drawdown")
                break
    if drawdown is not None:
        penalties["drawdown"] = normalize(drawdown, 0.0, 0.5) * 100.0

    if not penalties:
        return 0.0

    component_weights = {"sector": 0.40, "geo": 0.35, "drawdown": 0.25}
    active_weight = sum(component_weights[k] for k in penalties)
    composite = sum(
        (component_weights[k] / active_weight) * penalties[k] for k in penalties
    )
    return composite


def _get_sector_for_symbol(symbol: str, holdings: list[dict]) -> str | None:
    for h in holdings:
        if h.get("symbol") == symbol:
            return h.get("sector")
    return None


def _get_geo_for_symbol(symbol: str, holdings: list[dict]) -> str | None:
    for h in holdings:
        if h.get("symbol") == symbol:
            return h.get("geo")
    return None


# ---------------------------------------------------------------------------
# Main scoring
# ---------------------------------------------------------------------------


def score_ticker(
    symbol: str,
    fundamentals: dict | None,
    momentum: dict | None,
    all_momentum: list[dict],
    sector_medians: dict | None,
    holdings: list[dict] | None = None,
    portfolio_value: float = 0,
    risk_profile: str = "moderate",
    config: dict | None = None,
    sentiment: float | None = None,
) -> dict:
    """Score a single ticker on a 0-100 composite scale.

    Returns a dict with keys:
        symbol, score, valuation, momentum, risk_penalty, sentiment
    """
    use_sentiment = sentiment is not None
    weights = dict(_WEIGHTS_WITH_SENTIMENT if use_sentiment else _WEIGHTS_BASE)

    composites: dict[str, float | None] = {
        "valuation": compute_valuation_composite(fundamentals, sector_medians),
        "momentum": compute_momentum_composite(momentum, all_momentum),
        "risk": compute_risk_penalty(symbol, holdings, portfolio_value, config),
    }
    if use_sentiment:
        composites["sentiment"] = sentiment

    # For risk, the penalty *reduces* the score.  We invert it so the
    # weighted-sum formula can treat every signal uniformly (higher = better).
    if composites["risk"] is not None:
        composites["risk"] = 100.0 - composites["risk"]

    # Redistribute weight of any None composite to the remaining signals.
    none_keys = [k for k, v in composites.items() if v is None]
    active_keys = [k for k, v in composites.items() if v is not None]

    if not active_keys:
        # Nothing computable — return a neutral score.
        return {
            "symbol": symbol,
            "score": 50.0,
            "valuation": None,
            "momentum": None,
            "risk_penalty": 0.0,
            "sentiment": sentiment,
        }

    redistributed_weight = sum(weights[k] for k in none_keys)
    active_total = sum(weights[k] for k in active_keys)
    scale = (active_total + redistributed_weight) / active_total

    score = sum(weights[k] * scale * composites[k] for k in active_keys)
    score = max(0.0, min(100.0, score))

    return {
        "symbol": symbol,
        "score": round(score, 2),
        "valuation": (
            round(composites["valuation"], 2)
            if composites.get("valuation") is not None
            else None
        ),
        "momentum": (
            round(composites["momentum"], 2)
            if composites.get("momentum") is not None
            else None
        ),
        "risk_penalty": round(
            100.0 - composites["risk"], 2
        ) if composites.get("risk") is not None else 0.0,
        "sentiment": sentiment,
    }


# ---------------------------------------------------------------------------
# Universe scoring
# ---------------------------------------------------------------------------


def score_universe(
    symbols: list[str],
    holdings: list[dict] | None = None,
    portfolio_value: float = 0,
    risk_profile: str = "moderate",
    config: dict | None = None,
) -> list[dict]:
    """Score every symbol and return results sorted by score descending."""
    if not symbols:
        return []

    batch_fundamentals = market_data.get_batch_fundamentals(symbols)
    sector_medians = market_data.get_sector_medians()

    # Gather momentum signals for the full universe so percentile ranks are
    # computed against the complete peer set.
    momentum_by_symbol: dict[str, dict | None] = {}
    for sym in symbols:
        try:
            momentum_by_symbol[sym] = market_data.get_momentum_signals(sym)
        except Exception:
            logger.warning("Failed to fetch momentum for %s", sym, exc_info=True)
            momentum_by_symbol[sym] = None

    all_momentum = [m for m in momentum_by_symbol.values() if m is not None]

    results: list[dict] = []
    for sym in symbols:
        try:
            result = score_ticker(
                symbol=sym,
                fundamentals=batch_fundamentals.get(sym),
                momentum=momentum_by_symbol.get(sym),
                all_momentum=all_momentum,
                sector_medians=sector_medians,
                holdings=holdings,
                portfolio_value=portfolio_value,
                risk_profile=risk_profile,
                config=config,
            )
            results.append(result)
        except Exception:
            logger.error("Scoring failed for %s", sym, exc_info=True)

    results.sort(key=lambda r: r["score"], reverse=True)
    return results
