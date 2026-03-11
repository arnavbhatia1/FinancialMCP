"""FinancialMCP — MCP server for AI-powered stock analysis and paper trading."""

import json
import logging
import os
import sys

import yaml
from mcp.server.fastmcp import FastMCP

# Ensure package is importable when run as a script
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from financial_mcp import db, market_data, engine, risk, broker, portfolio

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    for candidate in [
        os.path.join(_root, "config.yaml"),
        os.path.join(os.getcwd(), "config.yaml"),
    ]:
        if os.path.exists(candidate):
            with open(candidate) as f:
                return yaml.safe_load(f)
    return {}


_config = _load_config()
_server_cfg = _config.get("server", {})

# Set DB path from config
_db_path = _config.get("database", {}).get("path", "data/financial_mcp.db")
if not os.path.isabs(_db_path):
    _db_path = os.path.join(_root, _db_path)
db.set_db_path(_db_path)

# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    _server_cfg.get("name", "financial-mcp"),
    host=_server_cfg.get("host", "0.0.0.0"),
    port=_server_cfg.get("port", 8520),
)

# Initialize DB on import
db.init_db()


# ── Helper ────────────────────────────────────────────────────────────────────

def _json(obj) -> str:
    """Serialize to compact JSON, handling None gracefully."""
    return json.dumps(obj, default=str)


def _error(tool_name: str, msg: str) -> str:
    return _json({"error": msg, "tool": tool_name})


# ── High-Level Orchestration Tools ────────────────────────────────────────────

@mcp.tool()
def analyze_ticker(symbol: str) -> str:
    """Full analysis of a ticker: fundamentals, momentum, and composite score."""
    try:
        symbol = symbol.upper().strip()
        fundamentals = market_data.get_fundamentals(symbol)
        momentum = market_data.get_momentum_signals(symbol)
        price = market_data.get_current_price(symbol)

        if fundamentals is None and momentum is None and price is None:
            return _error("analyze_ticker", f"No data available for {symbol}")

        # Score with no portfolio context
        batch = market_data.get_batch_fundamentals([symbol])
        sector_medians = market_data.get_sector_medians(batch)
        all_momentum = [momentum] if momentum else []

        score_result = engine.score_ticker(
            symbol=symbol,
            fundamentals=fundamentals,
            momentum=momentum,
            all_momentum=all_momentum,
            sector_medians=sector_medians,
            config=_config,
        )

        return _json({
            "symbol": symbol,
            "price": price,
            "fundamentals": fundamentals,
            "momentum": momentum,
            "score": score_result,
        })
    except Exception as e:
        logger.exception("analyze_ticker failed for %s", symbol)
        return _error("analyze_ticker", str(e))


@mcp.tool()
def analyze_portfolio(portfolio_id: str) -> str:
    """Portfolio summary with holdings, allocations, performance, and risk."""
    try:
        summary = portfolio.get_summary(portfolio_id)
        if summary is None:
            return _error("analyze_portfolio", f"Portfolio {portfolio_id} not found")

        perf = portfolio.compute_performance(portfolio_id)
        holdings_list = db.get_holdings(portfolio_id)
        stress = risk.compute_stress_score(
            holdings_list, summary["total_value"], _config
        )

        return _json({
            "portfolio": summary["portfolio"],
            "total_value": summary["total_value"],
            "holdings_value": summary["holdings_value"],
            "daily_change": summary["daily_change"],
            "daily_change_pct": summary["daily_change_pct"],
            "holdings": summary["holdings"],
            "sector_allocation": summary["sector_allocation"],
            "geo_allocation": summary["geo_allocation"],
            "performance": perf,
            "risk": stress,
        })
    except Exception as e:
        logger.exception("analyze_portfolio failed for %s", portfolio_id)
        return _error("analyze_portfolio", str(e))


@mcp.tool()
def run_rebalance(portfolio_id: str, trigger: str = "agent", symbols: str = "") -> str:
    """Run a full rebalance cycle: score universe, generate signals, execute trades.

    Args:
        portfolio_id: The portfolio to rebalance.
        trigger: What triggered this rebalance (e.g. 'agent', 'manual', 'scheduled').
        symbols: Comma-separated list of symbols to score. If empty, uses current holdings + common large-caps.
    """
    try:
        port = db.get_portfolio(portfolio_id)
        if port is None:
            return _error("run_rebalance", f"Portfolio {portfolio_id} not found")

        risk_profile = port["risk_profile"]
        holdings_list = db.get_holdings(portfolio_id)

        # Build symbol universe
        if symbols.strip():
            universe = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        else:
            # Current holdings + some defaults
            held = [h["symbol"] for h in holdings_list]
            defaults = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META",
                        "TSLA", "JPM", "V", "JNJ", "SPY", "VOO", "VTI",
                        "QQQ", "BND", "SCHD"]
            universe = list(set(held + defaults))

        # Score universe
        scores = engine.score_universe(
            symbols=universe,
            holdings=holdings_list,
            portfolio_value=port["current_cash"] + sum(
                h["shares"] * h["avg_cost_basis"] for h in holdings_list
            ),
            risk_profile=risk_profile,
            config=_config,
        )

        scoring_cfg = _config.get("scoring", {})
        buy_threshold = scoring_cfg.get("buy_threshold", 65)
        sell_threshold = scoring_cfg.get("sell_threshold", 35)

        # Generate buy/sell signals
        held_symbols = {h["symbol"] for h in holdings_list}
        buys = []
        sells = []

        for scored in scores:
            sym = scored["symbol"]
            sc = scored["score"]
            if sc >= buy_threshold and sym not in held_symbols:
                buys.append(scored)
            elif sc <= sell_threshold and sym in held_symbols:
                sells.append(scored)

        # Execute sells first
        paper = broker.PaperBroker(portfolio_id)
        executed_trades = []

        for sell in sells:
            holding = next((h for h in holdings_list if h["symbol"] == sell["symbol"]), None)
            if holding:
                result = paper.execute_sell(sell["symbol"], int(holding["shares"]))
                executed_trades.append({
                    "action": "sell", "symbol": sell["symbol"],
                    "result": result, "score": sell["score"],
                })

        # Execute buys with position sizing
        port_refreshed = db.get_portfolio(portfolio_id)
        available_cash = port_refreshed["current_cash"]
        position_limits = _config.get("position_limits", {}).get(risk_profile, {})
        max_position_pct = position_limits.get("max_position", 0.08)
        min_cash_pct = position_limits.get("min_cash", 0.10)
        portfolio_value = port_refreshed["current_cash"] + sum(
            h["shares"] * h["avg_cost_basis"] for h in db.get_holdings(portfolio_id)
        )
        min_cash = portfolio_value * min_cash_pct
        deploy_budget = max(0, available_cash - min_cash) * 0.80

        if buys and deploy_budget > 0:
            total_score = sum(b["score"] for b in buys)
            for buy in buys:
                if total_score <= 0:
                    break
                weight = buy["score"] / total_score
                allocation = min(
                    deploy_budget * weight,
                    portfolio_value * max_position_pct,
                )
                price = market_data.get_current_price(buy["symbol"])
                if price and price > 0:
                    shares = int(allocation / price)
                    if shares > 0:
                        result = paper.execute_buy(buy["symbol"], shares)
                        executed_trades.append({
                            "action": "buy", "symbol": buy["symbol"],
                            "result": result, "score": buy["score"],
                        })

        # Take snapshot after rebalance
        snapshot = portfolio.take_snapshot(portfolio_id)
        db.update_portfolio(portfolio_id, last_rebalanced_at=snapshot["snapshot_date"])

        return _json({
            "portfolio_id": portfolio_id,
            "trigger": trigger,
            "universe_scored": len(scores),
            "buy_signals": len(buys),
            "sell_signals": len(sells),
            "trades_executed": executed_trades,
            "snapshot": snapshot,
        })
    except Exception as e:
        logger.exception("run_rebalance failed for %s", portfolio_id)
        return _error("run_rebalance", str(e))


@mcp.tool()
def scan_universe(symbols: str) -> str:
    """Score a list of tickers and return them ranked by composite score.

    Args:
        symbols: Comma-separated list of ticker symbols (e.g. "AAPL,MSFT,GOOGL").
    """
    try:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if not symbol_list:
            return _error("scan_universe", "No symbols provided")

        scores = engine.score_universe(
            symbols=symbol_list,
            config=_config,
        )
        return _json({"count": len(scores), "scores": scores})
    except Exception as e:
        logger.exception("scan_universe failed")
        return _error("scan_universe", str(e))


# ── Fine-Grained Primitive Tools ──────────────────────────────────────────────

@mcp.tool()
def create_portfolio(
    starting_capital: float,
    risk_profile: str,
    investment_horizon: str,
    name: str = "Default",
) -> str:
    """Create a new paper trading portfolio.

    Args:
        starting_capital: Initial cash amount (10000-1000000).
        risk_profile: One of 'conservative', 'moderate', 'aggressive'.
        investment_horizon: One of 'short', 'medium', 'long'.
        name: Optional portfolio name.
    """
    try:
        pid = portfolio.create_portfolio(starting_capital, risk_profile, investment_horizon, name)
        port = db.get_portfolio(pid)
        return _json(port)
    except ValueError as e:
        return _error("create_portfolio", str(e))
    except Exception as e:
        logger.exception("create_portfolio failed")
        return _error("create_portfolio", str(e))


@mcp.tool()
def get_fundamentals(symbol: str) -> str:
    """Get fundamental metrics for a ticker: PE, EV/EBITDA, P/B, dividend yield, market cap, sector."""
    try:
        data = market_data.get_fundamentals(symbol.upper().strip())
        if data is None:
            return _error("get_fundamentals", f"No fundamentals for {symbol}")
        return _json(data)
    except Exception as e:
        logger.exception("get_fundamentals failed for %s", symbol)
        return _error("get_fundamentals", str(e))


@mcp.tool()
def get_momentum(symbol: str) -> str:
    """Get momentum signals: 30d/90d price momentum, volatility, relative strength, max drawdown."""
    try:
        data = market_data.get_momentum_signals(symbol.upper().strip())
        if data is None:
            return _error("get_momentum", f"No momentum data for {symbol}")
        return _json(data)
    except Exception as e:
        logger.exception("get_momentum failed for %s", symbol)
        return _error("get_momentum", str(e))


@mcp.tool()
def get_price(symbol: str) -> str:
    """Get the current price for a ticker symbol."""
    try:
        price = market_data.get_current_price(symbol.upper().strip())
        if price is None:
            return _error("get_price", f"No price data for {symbol}")
        return _json({"symbol": symbol.upper().strip(), "price": price})
    except Exception as e:
        logger.exception("get_price failed for %s", symbol)
        return _error("get_price", str(e))


@mcp.tool()
def score_ticker(symbol: str, sentiment: str = "") -> str:
    """Score a single ticker (0-100) with component breakdown.

    Args:
        symbol: Ticker symbol.
        sentiment: Optional JSON dict with sentiment data (e.g. '{"score": 75}').
    """
    try:
        symbol = symbol.upper().strip()
        fundamentals = market_data.get_fundamentals(symbol)
        momentum = market_data.get_momentum_signals(symbol)
        batch = market_data.get_batch_fundamentals([symbol])
        sector_medians = market_data.get_sector_medians(batch)
        all_momentum = [momentum] if momentum else []

        sentiment_data = None
        if sentiment.strip():
            sentiment_data = json.loads(sentiment)

        result = engine.score_ticker(
            symbol=symbol,
            fundamentals=fundamentals,
            momentum=momentum,
            all_momentum=all_momentum,
            sector_medians=sector_medians,
            config=_config,
            sentiment=sentiment_data,
        )
        return _json(result)
    except Exception as e:
        logger.exception("score_ticker failed for %s", symbol)
        return _error("score_ticker", str(e))


@mcp.tool()
def get_holdings(portfolio_id: str) -> str:
    """List current holdings in a portfolio with values and metadata."""
    try:
        port = db.get_portfolio(portfolio_id)
        if port is None:
            return _error("get_holdings", f"Portfolio {portfolio_id} not found")
        holdings = db.get_holdings(portfolio_id)
        return _json({"portfolio_id": portfolio_id, "count": len(holdings), "holdings": holdings})
    except Exception as e:
        logger.exception("get_holdings failed for %s", portfolio_id)
        return _error("get_holdings", str(e))


@mcp.tool()
def get_trades(portfolio_id: str, status: str = "") -> str:
    """Get trade history for a portfolio, optionally filtered by status.

    Args:
        portfolio_id: The portfolio ID.
        status: Optional filter: 'executed', 'proposed', 'rejected'. Empty for all.
    """
    try:
        port = db.get_portfolio(portfolio_id)
        if port is None:
            return _error("get_trades", f"Portfolio {portfolio_id} not found")
        status_filter = status.strip() if status.strip() else None
        trades = db.get_trades(portfolio_id, status=status_filter)
        return _json({"portfolio_id": portfolio_id, "count": len(trades), "trades": trades})
    except Exception as e:
        logger.exception("get_trades failed for %s", portfolio_id)
        return _error("get_trades", str(e))


@mcp.tool()
def execute_buy(portfolio_id: str, symbol: str, shares: int) -> str:
    """Buy shares of a stock/ETF in a portfolio.

    Args:
        portfolio_id: The portfolio to trade in.
        symbol: Ticker symbol to buy.
        shares: Number of whole shares to buy.
    """
    try:
        paper = broker.PaperBroker(portfolio_id)
        result = paper.execute_buy(symbol.upper().strip(), int(shares))
        return _json(result)
    except ValueError as e:
        return _error("execute_buy", str(e))
    except Exception as e:
        logger.exception("execute_buy failed")
        return _error("execute_buy", str(e))


@mcp.tool()
def execute_sell(portfolio_id: str, symbol: str, shares: int) -> str:
    """Sell shares of a stock/ETF from a portfolio.

    Args:
        portfolio_id: The portfolio to trade in.
        symbol: Ticker symbol to sell.
        shares: Number of whole shares to sell.
    """
    try:
        paper = broker.PaperBroker(portfolio_id)
        result = paper.execute_sell(symbol.upper().strip(), int(shares))
        return _json(result)
    except ValueError as e:
        return _error("execute_sell", str(e))
    except Exception as e:
        logger.exception("execute_sell failed")
        return _error("execute_sell", str(e))


@mcp.tool()
def check_risk(portfolio_id: str) -> str:
    """Assess portfolio risk: stress score, scenario drawdowns, sector/geo allocation."""
    try:
        port = db.get_portfolio(portfolio_id)
        if port is None:
            return _error("check_risk", f"Portfolio {portfolio_id} not found")

        holdings_list = db.get_holdings(portfolio_id)
        portfolio_value = port["current_cash"] + sum(
            h["shares"] * h["avg_cost_basis"] for h in holdings_list
        )

        stress = risk.compute_stress_score(holdings_list, portfolio_value, _config)
        sector_alloc = risk.get_sector_allocation(holdings_list, portfolio_value)
        geo_alloc = risk.get_geo_allocation(holdings_list, portfolio_value)

        return _json({
            "portfolio_id": portfolio_id,
            "portfolio_value": portfolio_value,
            "stress": stress,
            "sector_allocation": sector_alloc,
            "geo_allocation": geo_alloc,
        })
    except Exception as e:
        logger.exception("check_risk failed for %s", portfolio_id)
        return _error("check_risk", str(e))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    """Run the MCP server with SSE transport."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("Starting FinancialMCP server on %s:%s",
                _server_cfg.get("host", "0.0.0.0"), _server_cfg.get("port", 8520))
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
