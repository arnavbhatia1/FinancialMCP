"""Portfolio management — create, summarize, measure, and snapshot portfolios."""

import logging
import math
import uuid
from datetime import datetime, timezone

from . import db, market_data

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

VALID_RISK_PROFILES = {"conservative", "moderate", "aggressive"}
VALID_HORIZONS = {"short", "medium", "long"}
MIN_CAPITAL = 10_000.0
MAX_CAPITAL = 1_000_000.0

_RISK_FREE_DAILY = 0.05 / 252  # 5% annualized, per trading day
_TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_portfolio(
    starting_capital: float,
    risk_profile: str,
    horizon: str,
    name: str | None = None,
) -> str:
    """Create a new portfolio after validating inputs. Returns the portfolio id."""
    if not (MIN_CAPITAL <= starting_capital <= MAX_CAPITAL):
        raise ValueError(
            f"starting_capital must be between {MIN_CAPITAL:,.0f} and "
            f"{MAX_CAPITAL:,.0f}, got {starting_capital:,.2f}"
        )
    if risk_profile not in VALID_RISK_PROFILES:
        raise ValueError(
            f"risk_profile must be one of {sorted(VALID_RISK_PROFILES)}, "
            f"got {risk_profile!r}"
        )
    if horizon not in VALID_HORIZONS:
        raise ValueError(
            f"horizon must be one of {sorted(VALID_HORIZONS)}, got {horizon!r}"
        )

    return db.create_portfolio(
        starting_capital=starting_capital,
        risk_profile=risk_profile,
        horizon=horizon,
        name=name,
    )


def get_summary(portfolio_id: str) -> dict | None:
    """Build a full portfolio summary with live-priced holdings and allocations.

    Returns None if the portfolio does not exist.
    """
    portfolio = db.get_portfolio(portfolio_id)
    if portfolio is None:
        return None

    holdings = db.get_holdings(portfolio_id)
    cash = portfolio["current_cash"]

    # Enrich each holding with live pricing.
    enriched_holdings: list[dict] = []
    holdings_value = 0.0

    for h in holdings:
        current_price = market_data.get_current_price(h["symbol"])
        if current_price is None:
            current_price = h["avg_cost_basis"]

        current_value = current_price * h["shares"]
        cost_basis_total = h["avg_cost_basis"] * h["shares"]
        gain_loss = current_value - cost_basis_total
        holdings_value += current_value

        enriched_holdings.append(
            {
                **h,
                "current_price": current_price,
                "current_value": round(current_value, 2),
                "gain_loss": round(gain_loss, 2),
                "weight": 0.0,  # placeholder; computed after total_value is known
            }
        )

    total_value = cash + holdings_value

    # Compute weights and allocation breakdowns.
    sector_allocation: dict[str, float] = {}
    geo_allocation: dict[str, float] = {}

    for eh in enriched_holdings:
        eh["weight"] = round(eh["current_value"] / total_value, 4) if total_value else 0.0

        sector = eh.get("sector") or "unknown"
        geo = eh.get("geography") or "unknown"
        sector_allocation[sector] = sector_allocation.get(sector, 0.0) + eh["current_value"]
        geo_allocation[geo] = geo_allocation.get(geo, 0.0) + eh["current_value"]

    if total_value:
        sector_allocation = {k: round(v / total_value, 4) for k, v in sector_allocation.items()}
        geo_allocation = {k: round(v / total_value, 4) for k, v in geo_allocation.items()}

    # Daily change from most recent snapshot.
    daily_change = 0.0
    daily_change_pct = 0.0
    snapshots = db.get_snapshots(portfolio_id, limit=1)
    if snapshots:
        prev_value = snapshots[0]["total_value"]
        daily_change = round(total_value - prev_value, 2)
        if prev_value:
            daily_change_pct = round(daily_change / prev_value, 4)

    return {
        "portfolio": portfolio,
        "holdings": enriched_holdings,
        "total_value": round(total_value, 2),
        "holdings_value": round(holdings_value, 2),
        "daily_change": daily_change,
        "daily_change_pct": daily_change_pct,
        "sector_allocation": sector_allocation,
        "geo_allocation": geo_allocation,
    }


def compute_performance(portfolio_id: str) -> dict:
    """Compute key performance metrics from snapshot history.

    Returns sensible zero-defaults when no snapshots are available.
    """
    defaults = {
        "cumulative_return": 0.0,
        "daily_return": 0.0,
        "benchmark_return": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
    }

    portfolio = db.get_portfolio(portfolio_id)
    if portfolio is None:
        return defaults

    starting_capital = portfolio["starting_capital"]
    snapshots = db.get_snapshots(portfolio_id, limit=365)  # newest-first
    if not snapshots:
        return defaults

    # Work in chronological order.
    chronological = list(reversed(snapshots))
    values = [s["total_value"] for s in chronological]
    latest_value = values[-1]

    # -- Cumulative return -----------------------------------------------------
    cumulative_return = (latest_value / starting_capital - 1) if starting_capital else 0.0

    # -- Daily return (last two snapshots) -------------------------------------
    daily_return = 0.0
    if len(values) >= 2 and values[-2]:
        daily_return = values[-1] / values[-2] - 1

    # -- Benchmark return (SPY over same period) -------------------------------
    benchmark_return = _compute_benchmark_return(chronological)

    # -- Sharpe ratio ----------------------------------------------------------
    sharpe_ratio = _compute_sharpe(values)

    # -- Max drawdown ----------------------------------------------------------
    max_drawdown = _compute_max_drawdown(values)

    return {
        "cumulative_return": round(cumulative_return, 4),
        "daily_return": round(daily_return, 4),
        "benchmark_return": round(benchmark_return, 4),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "max_drawdown": round(max_drawdown, 4),
    }


def take_snapshot(portfolio_id: str) -> dict:
    """Capture current state and performance, persist to db, and return the snapshot."""
    summary = get_summary(portfolio_id)
    if summary is None:
        raise ValueError(f"Portfolio {portfolio_id!r} not found")

    performance = compute_performance(portfolio_id)
    portfolio = summary["portfolio"]

    snapshot = {
        "id": str(uuid.uuid4()),
        "portfolio_id": portfolio_id,
        "snapshot_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_value": summary["total_value"],
        "cash_value": portfolio["current_cash"],
        "holdings_value": summary["holdings_value"],
        "daily_return": performance["daily_return"],
        "cumulative_return": performance["cumulative_return"],
        "benchmark_return": performance["benchmark_return"],
        "sharpe_ratio": performance["sharpe_ratio"],
        "max_drawdown": performance["max_drawdown"],
        "sector_allocation": summary["sector_allocation"],
        "geo_allocation": summary["geo_allocation"],
    }

    db.save_snapshot(snapshot)
    return snapshot


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_benchmark_return(chronological_snapshots: list[dict]) -> float:
    """SPY total return over the same date range as the snapshots."""
    if len(chronological_snapshots) < 2:
        return 0.0

    start_date = chronological_snapshots[0]["snapshot_date"]
    end_date = chronological_snapshots[-1]["snapshot_date"]

    try:
        import yfinance as yf

        hist = yf.Ticker("SPY").history(start=start_date, end=end_date)
        if hist.empty or len(hist) < 2:
            return 0.0
        return float(hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1)
    except Exception:
        logger.debug("Benchmark (SPY) fetch failed; returning 0.0")
        return 0.0


def _compute_sharpe(values: list[float]) -> float:
    """Annualized Sharpe ratio from a chronological list of portfolio values."""
    if len(values) < 3:
        return 0.0

    daily_returns = [
        values[i] / values[i - 1] - 1
        for i in range(1, len(values))
        if values[i - 1]
    ]

    if len(daily_returns) < 2:
        return 0.0

    mean_return = sum(daily_returns) / len(daily_returns)
    excess_returns = [r - _RISK_FREE_DAILY for r in daily_returns]
    mean_excess = sum(excess_returns) / len(excess_returns)

    variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_dev = math.sqrt(variance)

    if std_dev == 0:
        return 0.0

    return (mean_excess / std_dev) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def _compute_max_drawdown(values: list[float]) -> float:
    """Worst peak-to-trough decline from a chronological list of portfolio values."""
    if len(values) < 2:
        return 0.0

    peak = values[0]
    max_dd = 0.0

    for v in values[1:]:
        if v > peak:
            peak = v
        if peak:
            dd = (v - peak) / peak
            if dd < max_dd:
                max_dd = dd

    return max_dd
