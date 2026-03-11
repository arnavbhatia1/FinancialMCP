"""Risk assessment: allocation checks, position limits, and stress testing."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Historical sector drawdown multipliers for three crisis scenarios.
# Sources: S&P 500 GICS sector returns during 2008 GFC, 2020 COVID crash,
# and 2022 rate-hike selloff.  Negative values indicate a sector *gained*.
SECTOR_SENSITIVITY: dict[str, dict[str, float]] = {
    "Technology":             {"2008": 0.49, "2020": 0.32, "2022": 0.33},
    "Financial Services":     {"2008": 0.75, "2020": 0.38, "2022": 0.20},
    "Healthcare":             {"2008": 0.35, "2020": 0.15, "2022": 0.12},
    "Consumer Cyclical":      {"2008": 0.55, "2020": 0.40, "2022": 0.25},
    "Consumer Defensive":     {"2008": 0.25, "2020": 0.12, "2022": 0.08},
    "Industrials":            {"2008": 0.50, "2020": 0.35, "2022": 0.18},
    "Energy":                 {"2008": 0.42, "2020": 0.55, "2022": -0.15},
    "Utilities":              {"2008": 0.30, "2020": 0.18, "2022": 0.05},
    "Real Estate":            {"2008": 0.60, "2020": 0.25, "2022": 0.28},
    "Communication Services": {"2008": 0.40, "2020": 0.20, "2022": 0.38},
    "Basic Materials":        {"2008": 0.48, "2020": 0.30, "2022": 0.15},
}

_DEFAULT_SENSITIVITY: dict[str, float] = {"2008": 0.50, "2020": 0.30, "2022": 0.20}

# Fallback position limits when no config is provided.
_DEFAULT_POSITION_LIMITS: dict[str, float] = {
    "max_position": 0.08,
    "max_sector": 0.30,
}


# ---------------------------------------------------------------------------
# Allocation helpers
# ---------------------------------------------------------------------------


def _holding_value(holding: dict) -> float:
    """Proxy current value as shares * avg_cost_basis."""
    return holding.get("shares", 0) * holding.get("avg_cost_basis", 0)


def get_sector_allocation(
    holdings: list[dict],
    portfolio_value: float,
) -> dict[str, float]:
    """Return a mapping of sector -> weight (0-1) in the portfolio.

    Uses shares * avg_cost_basis as a proxy for current market value.
    Returns an empty dict when *portfolio_value* is zero or negative.
    """
    if portfolio_value <= 0:
        return {}

    sector_totals: dict[str, float] = {}
    for h in holdings:
        sector = h.get("sector") or "Unknown"
        sector_totals[sector] = sector_totals.get(sector, 0) + _holding_value(h)

    return {
        sector: total / portfolio_value
        for sector, total in sector_totals.items()
    }


def get_geo_allocation(
    holdings: list[dict],
    portfolio_value: float,
) -> dict[str, float]:
    """Return a mapping of geography -> weight (0-1) in the portfolio.

    Returns an empty dict when *portfolio_value* is zero or negative.
    """
    if portfolio_value <= 0:
        return {}

    geo_totals: dict[str, float] = {}
    for h in holdings:
        geo = h.get("geography") or "Unknown"
        geo_totals[geo] = geo_totals.get(geo, 0) + _holding_value(h)

    return {
        geo: total / portfolio_value
        for geo, total in geo_totals.items()
    }


# ---------------------------------------------------------------------------
# Position-limit checks
# ---------------------------------------------------------------------------


def check_position_limits(
    symbol: str,
    proposed_value: float,
    holdings: list[dict],
    portfolio_value: float,
    risk_profile: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a proposed position against risk-profile limits.

    Returns ``{"allowed": True/False, "violations": [...]}``.
    """
    violations: list[str] = []

    if portfolio_value <= 0:
        violations.append("Portfolio value is zero or negative")
        return {"allowed": False, "violations": violations}

    # Resolve limits from config or fall back to moderate defaults.
    limits = _DEFAULT_POSITION_LIMITS
    if config is not None:
        profile_limits = (
            config.get("position_limits", {}).get(risk_profile)
        )
        if profile_limits is not None:
            limits = profile_limits

    max_position = limits.get("max_position", _DEFAULT_POSITION_LIMITS["max_position"])
    max_sector = limits.get("max_sector", _DEFAULT_POSITION_LIMITS["max_sector"])

    # --- single-position concentration ---
    position_weight = proposed_value / portfolio_value
    if position_weight > max_position:
        violations.append(
            f"{symbol} position would be {position_weight:.1%} of portfolio, "
            f"exceeding {risk_profile} limit of {max_position:.0%}"
        )

    # --- sector concentration after trade ---
    # Determine the sector of the proposed symbol from existing holdings.
    proposed_sector: str | None = None
    for h in holdings:
        if h.get("symbol") == symbol:
            proposed_sector = h.get("sector")
            break

    if proposed_sector is not None:
        sector_alloc = get_sector_allocation(holdings, portfolio_value)
        current_sector_weight = sector_alloc.get(proposed_sector, 0)
        added_weight = proposed_value / portfolio_value
        new_sector_weight = current_sector_weight + added_weight
        if new_sector_weight > max_sector:
            violations.append(
                f"{proposed_sector} sector would be {new_sector_weight:.1%} of portfolio, "
                f"exceeding {risk_profile} limit of {max_sector:.0%}"
            )

    allowed = len(violations) == 0
    if not allowed:
        logger.warning(
            "Position-limit violations for %s: %s", symbol, violations
        )

    return {"allowed": allowed, "violations": violations}


# ---------------------------------------------------------------------------
# Stress testing
# ---------------------------------------------------------------------------


def compute_stress_score(
    holdings: list[dict],
    portfolio_value: float,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run crisis-scenario stress tests on the current portfolio.

    Returns::

        {
            "stress_score": float,          # max drawdown across scenarios (0-1)
            "scenario_drawdowns": {         # per-scenario portfolio drawdown
                "2008": float,
                "2020": float,
                "2022": float,
            },
            "vulnerable_sectors": [str],    # sectors with sensitivity > 0.40
        }
    """
    if not holdings or portfolio_value <= 0:
        return {
            "stress_score": 0.0,
            "scenario_drawdowns": {"2008": 0.0, "2020": 0.0, "2022": 0.0},
            "vulnerable_sectors": [],
        }

    sector_alloc = get_sector_allocation(holdings, portfolio_value)
    scenarios = ("2008", "2020", "2022")

    # Weighted drawdown per scenario.
    scenario_drawdowns: dict[str, float] = {}
    for scenario in scenarios:
        drawdown = 0.0
        for sector, weight in sector_alloc.items():
            sensitivity = SECTOR_SENSITIVITY.get(
                sector, _DEFAULT_SENSITIVITY
            )
            drawdown += weight * sensitivity.get(scenario, 0)
        scenario_drawdowns[scenario] = round(drawdown, 6)

    stress_score = max(scenario_drawdowns.values())

    # Identify the worst scenario, then find sectors with sensitivity > 0.40.
    worst_scenario = max(scenario_drawdowns, key=scenario_drawdowns.get)  # type: ignore[arg-type]
    vulnerable_sectors: list[str] = []
    for sector in sector_alloc:
        sensitivity = SECTOR_SENSITIVITY.get(sector, _DEFAULT_SENSITIVITY)
        if sensitivity.get(worst_scenario, 0) > 0.40:
            vulnerable_sectors.append(sector)

    logger.info(
        "Stress test: score=%.2f, worst=%s, vulnerable=%s",
        stress_score,
        worst_scenario,
        vulnerable_sectors,
    )

    return {
        "stress_score": round(stress_score, 6),
        "scenario_drawdowns": scenario_drawdowns,
        "vulnerable_sectors": sorted(vulnerable_sectors),
    }
