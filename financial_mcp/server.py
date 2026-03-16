"""FinancialMCP — MCP server for AI-powered stock market intelligence."""

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

from financial_mcp import market_data, engine, db, broker, portfolio, risk
from financial_mcp import sec_edgar, fred, cftc, trends, treasury, regime, anomaly

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
db.init_db()

# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    _server_cfg.get("name", "financial-mcp"),
    host=_server_cfg.get("host", "0.0.0.0"),
    port=_server_cfg.get("port", 8520),
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _json(obj) -> str:
    """Serialize to compact JSON, handling None gracefully."""
    return json.dumps(obj, default=str)


def _error(tool_name: str, msg: str) -> str:
    return _json({"error": msg, "tool": tool_name})


def _parse_symbols(symbols: str) -> list[str] | None:
    """Parse a comma-separated symbol string into a cleaned list, or None if empty."""
    if not symbols.strip():
        return None
    return [s.strip().upper() for s in symbols.split(",") if s.strip()]


# ── Analysis & Scoring Tools ─────────────────────────────────────────────────

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
def scan_universe(symbols: str) -> str:
    """Score a list of tickers and return them ranked by composite score.

    Args:
        symbols: Comma-separated list of ticker symbols (e.g. "AAPL,MSFT,GOOGL").
    """
    try:
        symbol_list = _parse_symbols(symbols)
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


@mcp.tool()
def get_fundamentals(symbol: str) -> str:
    """Get fundamental metrics for a ticker: PE, EV/EBITDA, P/B, dividend yield, market cap, sector."""
    try:
        symbol = symbol.upper().strip()
        data = market_data.get_fundamentals(symbol)
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
        symbol = symbol.upper().strip()
        data = market_data.get_momentum_signals(symbol)
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
        symbol = symbol.upper().strip()
        price = market_data.get_current_price(symbol)
        if price is None:
            return _error("get_price", f"No price data for {symbol}")
        return _json({"symbol": symbol, "price": price})
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


# ── SEC EDGAR Tools ───────────────────────────────────────────────────────────

@mcp.tool()
def get_sec_filings(symbol: str, filing_type: str = "10-K", count: int = 5) -> str:
    """Get recent SEC filings for a company.

    Args:
        symbol: Ticker symbol.
        filing_type: Filing type (10-K, 10-Q, 8-K, etc).
        count: Number of filings to return.
    """
    try:
        symbol = symbol.upper().strip()
        filings = sec_edgar.get_filings(symbol, filing_type, count)
        if filings is None:
            return _error("get_sec_filings", f"No filings found for {symbol}")
        return _json({"symbol": symbol, "filing_type": filing_type, "filings": filings})
    except Exception as e:
        logger.exception("get_sec_filings failed")
        return _error("get_sec_filings", str(e))


@mcp.tool()
def get_insider_trades(symbol: str, days: int = 90) -> str:
    """Get recent insider trading filings (Forms 3/4/5) for a company.

    Args:
        symbol: Ticker symbol.
        days: Look back this many days.
    """
    try:
        symbol = symbol.upper().strip()
        trades = sec_edgar.get_insider_trades(symbol, days)
        if trades is None:
            return _error("get_insider_trades", f"No insider trades found for {symbol}")
        return _json({"symbol": symbol, "insider_trades": trades})
    except Exception as e:
        logger.exception("get_insider_trades failed")
        return _error("get_insider_trades", str(e))


@mcp.tool()
def search_sec_filings(query: str, filing_type: str = "", count: int = 10) -> str:
    """Search SEC EDGAR full-text for filings matching a query.

    Args:
        query: Search text (company name, keyword, ticker).
        filing_type: Optional filter (10-K, 10-Q, 8-K, etc). Empty for all types.
        count: Max results.
    """
    try:
        ft = filing_type.strip() or None
        results = sec_edgar.search_filings(query, filing_type=ft, count=count)
        if results is None:
            return _error("search_sec_filings", f"No results for '{query}'")
        return _json({"query": query, "count": len(results), "filings": results})
    except Exception as e:
        logger.exception("search_sec_filings failed")
        return _error("search_sec_filings", str(e))


# ── FRED Macro Tools ─────────────────────────────────────────────────────────

@mcp.tool()
def get_economic_indicator(series_id: str, limit: int = 100) -> str:
    """Get economic data from FRED (Federal Reserve). Common series: GDP, CPIAUCSL, UNRATE, FEDFUNDS, DFF, T10Y2Y, VIXCLS.

    Args:
        series_id: FRED series ID.
        limit: Number of observations.
    """
    try:
        data = fred.get_series(series_id.upper().strip(), limit=limit)
        if data is None:
            return _error("get_economic_indicator", f"No data for {series_id}")
        return _json(data)
    except Exception as e:
        logger.exception("get_economic_indicator failed")
        return _error("get_economic_indicator", str(e))


@mcp.tool()
def get_yield_curve() -> str:
    """Get the current US Treasury yield curve with inversion detection."""
    try:
        data = fred.get_yield_curve()
        if data is None:
            return _error("get_yield_curve", "Could not fetch yield curve")
        return _json(data)
    except Exception as e:
        logger.exception("get_yield_curve failed")
        return _error("get_yield_curve", str(e))


@mcp.tool()
def get_economic_snapshot() -> str:
    """Get a snapshot of key economic indicators: GDP, CPI, unemployment, fed funds, VIX, credit spreads."""
    try:
        data = fred.get_economic_snapshot()
        if data is None:
            return _error("get_economic_snapshot", "Could not fetch economic snapshot")
        return _json(data)
    except Exception as e:
        logger.exception("get_economic_snapshot failed")
        return _error("get_economic_snapshot", str(e))


# ── CFTC COT Tools ───────────────────────────────────────────────────────────

@mcp.tool()
def get_futures_positioning(market: str, limit: int = 10) -> str:
    """Get CFTC Commitments of Traders data showing how commercials and speculators are positioned.

    Args:
        market: Market name (e.g. GOLD, CRUDE OIL, E-MINI S&P 500, BITCOIN, EURO FX, CORN).
        limit: Number of weekly reports.
    """
    try:
        data = cftc.get_positioning(market, limit)
        if data is None:
            return _error("get_futures_positioning", f"No COT data for '{market}'")
        return _json(data)
    except Exception as e:
        logger.exception("get_futures_positioning failed")
        return _error("get_futures_positioning", str(e))


@mcp.tool()
def get_smart_money_signal(market: str) -> str:
    """Get a bullish/bearish/neutral signal based on commercial hedger positioning (the 'smart money').

    Args:
        market: Market name (e.g. GOLD, CRUDE OIL, E-MINI S&P 500).
    """
    try:
        data = cftc.get_smart_money_signal(market)
        if data is None:
            return _error("get_smart_money_signal", f"Could not compute signal for '{market}'")
        return _json(data)
    except Exception as e:
        logger.exception("get_smart_money_signal failed")
        return _error("get_smart_money_signal", str(e))


# ── Google Trends Tools ──────────────────────────────────────────────────────

@mcp.tool()
def get_search_trends(keywords: str, timeframe: str = "today 3-m") -> str:
    """Get Google Trends search interest over time. Useful as a leading indicator for market sentiment.

    Args:
        keywords: Comma-separated keywords (max 5). E.g. "recession,stock market,buy stocks".
        timeframe: Time range: 'now 7-d', 'today 1-m', 'today 3-m', 'today 12-m', 'today 5-y'.
    """
    try:
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()][:5]
        if not kw_list:
            return _error("get_search_trends", "No keywords provided")
        data = trends.get_search_interest(kw_list, timeframe)
        if data is None:
            return _error("get_search_trends", "Could not fetch trends data")
        return _json(data)
    except Exception as e:
        logger.exception("get_search_trends failed")
        return _error("get_search_trends", str(e))


@mcp.tool()
def get_trending_searches(country: str = "united_states") -> str:
    """Get currently trending Google searches for a country."""
    try:
        data = trends.get_trending_searches(country)
        if data is None:
            return _error("get_trending_searches", "Could not fetch trending searches")
        return _json({"country": country, "trending": data})
    except Exception as e:
        logger.exception("get_trending_searches failed")
        return _error("get_trending_searches", str(e))


# ── Treasury Tools ───────────────────────────────────────────────────────────

@mcp.tool()
def get_treasury_rates(days: int = 30) -> str:
    """Get recent US Treasury average interest rates."""
    try:
        data = treasury.get_treasury_rates(days)
        if data is None:
            return _error("get_treasury_rates", "Could not fetch treasury rates")
        return _json(data)
    except Exception as e:
        logger.exception("get_treasury_rates failed")
        return _error("get_treasury_rates", str(e))


@mcp.tool()
def get_treasury_yield_curve(days: int = 5) -> str:
    """Get daily Treasury yield curve data (1mo through 30yr maturities)."""
    try:
        data = treasury.get_yield_curve_daily(days)
        if data is None:
            return _error("get_treasury_yield_curve", "Could not fetch yield curve")
        return _json({"days": days, "curves": data})
    except Exception as e:
        logger.exception("get_treasury_yield_curve failed")
        return _error("get_treasury_yield_curve", str(e))


@mcp.tool()
def get_treasury_auctions(security_type: str = "", days: int = 30) -> str:
    """Get recent Treasury auction results.

    Args:
        security_type: Optional filter (e.g. 'Bill', 'Note', 'Bond', 'TIPS'). Empty for all.
        days: Look back period.
    """
    try:
        st = security_type.strip() or None
        data = treasury.get_treasury_auctions(st, days)
        if data is None:
            return _error("get_treasury_auctions", "Could not fetch auctions")
        return _json({"count": len(data), "auctions": data})
    except Exception as e:
        logger.exception("get_treasury_auctions failed")
        return _error("get_treasury_auctions", str(e))


# ── Market Regime Tools ──────────────────────────────────────────────────────

@mcp.tool()
def detect_market_regime() -> str:
    """Classify current market regime: BULL, BEAR, SIDEWAYS, HIGH_VOLATILITY, or CRASH. Uses SPY and VIX signals."""
    try:
        data = regime.detect_regime()
        if data is None:
            return _error("detect_market_regime", "Could not detect regime")
        return _json(data)
    except Exception as e:
        logger.exception("detect_market_regime failed")
        return _error("detect_market_regime", str(e))


@mcp.tool()
def get_regime_history(months: int = 12) -> str:
    """Get monthly market regime classification for the last N months.

    Args:
        months: Number of months of history.
    """
    try:
        data = regime.get_regime_history(months)
        if data is None:
            return _error("get_regime_history", "Could not compute regime history")
        return _json({"months": months, "history": data})
    except Exception as e:
        logger.exception("get_regime_history failed")
        return _error("get_regime_history", str(e))


@mcp.tool()
def get_vix_analysis() -> str:
    """Analyze VIX: current level, 1-year percentile, term structure, and fear signal."""
    try:
        data = regime.get_vix_analysis()
        if data is None:
            return _error("get_vix_analysis", "Could not analyze VIX")
        return _json(data)
    except Exception as e:
        logger.exception("get_vix_analysis failed")
        return _error("get_vix_analysis", str(e))


# ── Anomaly Scanner Tools ────────────────────────────────────────────────────

@mcp.tool()
def scan_anomalies(symbols: str = "", lookback_days: int = 30) -> str:
    """Scan for market anomalies: volume spikes, price gaps, 52-week extremes, volatility expansion, momentum divergence.

    Args:
        symbols: Comma-separated symbols. Empty uses 50 major tickers.
        lookback_days: Analysis window.
    """
    try:
        sym_list = _parse_symbols(symbols)
        data = anomaly.scan_anomalies(sym_list, lookback_days)
        return _json({"count": len(data), "anomalies": data})
    except Exception as e:
        logger.exception("scan_anomalies failed")
        return _error("scan_anomalies", str(e))


@mcp.tool()
def scan_volume_leaders(symbols: str = "", min_ratio: float = 2.0) -> str:
    """Find stocks with unusual volume (today's volume vs 20-day average).

    Args:
        symbols: Comma-separated symbols. Empty uses 50 major tickers.
        min_ratio: Minimum volume ratio to include (default 2x).
    """
    try:
        sym_list = _parse_symbols(symbols)
        data = anomaly.scan_volume_leaders(sym_list, min_ratio)
        return _json({"count": len(data), "leaders": data})
    except Exception as e:
        logger.exception("scan_volume_leaders failed")
        return _error("scan_volume_leaders", str(e))


@mcp.tool()
def scan_gap_movers(symbols: str = "", min_gap_pct: float = 2.0) -> str:
    """Find stocks that gapped significantly at market open.

    Args:
        symbols: Comma-separated symbols. Empty uses 50 major tickers.
        min_gap_pct: Minimum gap percentage to include.
    """
    try:
        sym_list = _parse_symbols(symbols)
        data = anomaly.scan_gap_movers(sym_list, min_gap_pct)
        return _json({"count": len(data), "movers": data})
    except Exception as e:
        logger.exception("scan_gap_movers failed")
        return _error("scan_gap_movers", str(e))


# ── Portfolio & Trading Tools ────────────────────────────────────────────────

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
def run_rebalance(portfolio_id: str, trigger: str = "agent", symbols: str = "") -> str:
    """Run a full rebalance cycle: score universe, generate signals, execute trades.

    Args:
        portfolio_id: The portfolio to rebalance.
        trigger: What triggered this rebalance (e.g. 'agent', 'manual', 'scheduled').
        symbols: Comma-separated list of symbols to score. If empty, uses current holdings + large-caps.
    """
    try:
        port = db.get_portfolio(portfolio_id)
        if port is None:
            return _error("run_rebalance", f"Portfolio {portfolio_id} not found")

        risk_profile = port["risk_profile"]
        holdings_list = db.get_holdings(portfolio_id)

        if symbols.strip():
            universe = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        else:
            held = [h["symbol"] for h in holdings_list]
            defaults = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META",
                        "TSLA", "JPM", "V", "JNJ", "SPY", "VOO", "VTI",
                        "QQQ", "BND", "SCHD"]
            universe = list(set(held + defaults))

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

        held_symbols = {h["symbol"] for h in holdings_list}
        buys, sells = [], []
        for scored in scores:
            sym, sc = scored["symbol"], scored["score"]
            if sc >= buy_threshold and sym not in held_symbols:
                buys.append(scored)
            elif sc <= sell_threshold and sym in held_symbols:
                sells.append(scored)

        paper = broker.PaperBroker(portfolio_id)
        executed_trades = []

        for sell in sells:
            holding = next((h for h in holdings_list if h["symbol"] == sell["symbol"]), None)
            if holding:
                result = paper.execute_sell(sell["symbol"], int(holding["shares"]))
                executed_trades.append({"action": "sell", "symbol": sell["symbol"],
                                        "result": result, "score": sell["score"]})

        port_refreshed = db.get_portfolio(portfolio_id)
        available_cash = port_refreshed["current_cash"]
        position_limits = _config.get("position_limits", {}).get(risk_profile, {})
        max_position_pct = position_limits.get("max_position", 0.08)
        min_cash_pct = position_limits.get("min_cash", 0.10)
        portfolio_value = port_refreshed["current_cash"] + sum(
            h["shares"] * h["avg_cost_basis"] for h in db.get_holdings(portfolio_id)
        )
        deploy_budget = max(0, available_cash - portfolio_value * min_cash_pct) * 0.80

        if buys and deploy_budget > 0:
            total_score = sum(b["score"] for b in buys)
            for buy in buys:
                if total_score <= 0:
                    break
                weight = buy["score"] / total_score
                allocation = min(deploy_budget * weight, portfolio_value * max_position_pct)
                price = market_data.get_current_price(buy["symbol"])
                if price and price > 0:
                    share_count = int(allocation / price)
                    if share_count > 0:
                        result = paper.execute_buy(buy["symbol"], share_count)
                        executed_trades.append({"action": "buy", "symbol": buy["symbol"],
                                                "result": result, "score": buy["score"]})

        snapshot = portfolio.take_snapshot(portfolio_id)
        db.update_portfolio(portfolio_id, last_rebalanced_at=snapshot["snapshot_date"])

        return _json({
            "portfolio_id": portfolio_id, "trigger": trigger,
            "universe_scored": len(scores),
            "buy_signals": len(buys), "sell_signals": len(sells),
            "trades_executed": executed_trades, "snapshot": snapshot,
        })
    except Exception as e:
        logger.exception("run_rebalance failed for %s", portfolio_id)
        return _error("run_rebalance", str(e))


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
            "portfolio_id": portfolio_id, "portfolio_value": portfolio_value,
            "stress": stress, "sector_allocation": sector_alloc,
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
