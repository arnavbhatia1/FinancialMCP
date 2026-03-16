"""Paper broker — executes simulated buy/sell trades against SQLite-backed portfolios."""

import logging
import uuid
from datetime import datetime

from . import db, market_data

logger = logging.getLogger(__name__)

COMMON_ETFS: set[str] = {
    "SPY", "QQQ", "IVV", "VOO", "VTI",
    "VXUS", "BND", "AGG", "GLD", "SLV",
    "TLT", "IEF", "SHY", "VNQ", "SCHD",
    "VIG", "VYM", "IWM", "DIA", "EFA",
}


class PaperBroker:
    """SQLite-backed paper trading broker.

    Each instance is bound to a single portfolio. Buy and sell methods
    validate balances/holdings, persist state to the database, and return
    a result dict describing success or failure.
    """

    def __init__(self, portfolio_id: str) -> None:
        self.portfolio_id = portfolio_id

        portfolio = db.get_portfolio(portfolio_id)
        if portfolio is None:
            raise ValueError(f"Portfolio not found: {portfolio_id}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_buy(
        self,
        symbol: str,
        shares: float,
    ) -> dict:
        """Buy *shares* of *symbol* at the current market price.

        Returns a dict with ``success`` and trade details (or ``error``).
        """
        symbol = symbol.upper()

        # --- Price lookup ---
        price = market_data.get_current_price(symbol)
        if price is None:
            return {"success": False, "error": f"Could not fetch price for {symbol}"}

        total_cost = shares * price

        # --- Cash check ---
        portfolio = db.get_portfolio(self.portfolio_id)
        if portfolio is None:
            return {"success": False, "error": "Portfolio no longer exists"}

        available_cash: float = portfolio["current_cash"]
        if available_cash < total_cost:
            return {
                "success": False,
                "error": (
                    f"Insufficient cash: need ${total_cost:,.2f} "
                    f"but only ${available_cash:,.2f} available"
                ),
            }

        # --- Determine asset metadata ---
        asset_type = "etf" if symbol in COMMON_ETFS else "stock"
        sector: str | None = None
        company_name: str | None = None

        fundamentals = market_data.get_fundamentals(symbol)
        if fundamentals:
            sector = fundamentals.get("sector")
            company_name = fundamentals.get("name")

        # --- Weighted average cost basis ---
        existing_holdings = db.get_holdings(self.portfolio_id)
        existing = next(
            (h for h in existing_holdings if h["symbol"] == symbol), None
        )

        if existing:
            old_shares: float = existing["shares"]
            old_avg: float = existing["avg_cost_basis"]
            new_avg = (old_shares * old_avg + shares * price) / (old_shares + shares)
            new_shares = old_shares + shares
        else:
            new_avg = price
            new_shares = shares

        now = datetime.utcnow().isoformat()

        # --- Persist ---
        db.upsert_holding(
            portfolio_id=self.portfolio_id,
            symbol=symbol,
            shares=new_shares,
            avg_cost_basis=new_avg,
            asset_type=asset_type,
            sector=sector,
            geography="us",
            company_name=company_name,
            acquired_at=now,
        )
        db.update_portfolio(
            self.portfolio_id,
            current_cash=available_cash - total_cost,
        )

        trade_id = str(uuid.uuid4())
        db.save_trade({
            "trade_id": trade_id,
            "portfolio_id": self.portfolio_id,
            "symbol": symbol,
            "action": "buy",
            "shares": shares,
            "price": price,
            "total_value": total_cost,
            "status": "executed",
            "trigger": "agent",
            "proposed_at": now,
            "executed_at": now,
        })

        logger.info(
            "BUY %s x%.4f @ $%.2f (total $%.2f) — portfolio %s",
            symbol, shares, price, total_cost, self.portfolio_id,
        )

        return {
            "success": True,
            "symbol": symbol,
            "shares": shares,
            "price": price,
            "total_cost": total_cost,
        }

    def execute_sell(
        self,
        symbol: str,
        shares: float,
    ) -> dict:
        """Sell *shares* of *symbol* at the current market price.

        Returns a dict with ``success`` and trade details (or ``error``).
        """
        symbol = symbol.upper()

        # --- Price lookup ---
        price = market_data.get_current_price(symbol)
        if price is None:
            return {"success": False, "error": f"Could not fetch price for {symbol}"}

        # --- Holding check ---
        existing_holdings = db.get_holdings(self.portfolio_id)
        holding = next(
            (h for h in existing_holdings if h["symbol"] == symbol), None
        )

        if holding is None:
            return {"success": False, "error": f"No holding found for {symbol}"}

        held_shares: float = holding["shares"]
        if held_shares < shares:
            return {
                "success": False,
                "error": (
                    f"Insufficient shares: trying to sell {shares} "
                    f"but only {held_shares} held"
                ),
            }

        total_proceeds = shares * price
        now = datetime.utcnow().isoformat()

        # --- Update or remove holding ---
        remaining = held_shares - shares
        if remaining == 0:
            db.delete_holding(self.portfolio_id, symbol)
        else:
            db.upsert_holding(
                portfolio_id=self.portfolio_id,
                symbol=symbol,
                shares=remaining,
                avg_cost_basis=holding["avg_cost_basis"],
                asset_type=holding.get("asset_type", "stock"),
                sector=holding.get("sector"),
                geography=holding.get("geography", "us"),
                company_name=holding.get("company_name"),
                acquired_at=holding.get("acquired_at", now),
            )

        # --- Credit cash ---
        portfolio = db.get_portfolio(self.portfolio_id)
        if portfolio is None:
            return {"success": False, "error": "Portfolio no longer exists"}

        db.update_portfolio(
            self.portfolio_id,
            current_cash=portfolio["current_cash"] + total_proceeds,
        )

        # --- Record trade ---
        trade_id = str(uuid.uuid4())
        db.save_trade({
            "trade_id": trade_id,
            "portfolio_id": self.portfolio_id,
            "symbol": symbol,
            "action": "sell",
            "shares": shares,
            "price": price,
            "total_value": total_proceeds,
            "status": "executed",
            "trigger": "agent",
            "proposed_at": now,
            "executed_at": now,
        })

        logger.info(
            "SELL %s x%.4f @ $%.2f (proceeds $%.2f) — portfolio %s",
            symbol, shares, price, total_proceeds, self.portfolio_id,
        )

        return {
            "success": True,
            "symbol": symbol,
            "shares": shares,
            "price": price,
            "total_proceeds": total_proceeds,
        }
