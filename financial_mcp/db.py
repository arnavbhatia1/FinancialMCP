"""SQLite storage layer for the Financial MCP server."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

_db_path: str = "data/financial_mcp.db"


def set_db_path(path: str) -> None:
    """Override the default database path."""
    global _db_path
    _db_path = path


def get_connection() -> sqlite3.Connection:
    """Return a connection with Row factory, creating parent dirs if needed."""
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create all tables if they don't already exist."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS portfolios (
                portfolio_id TEXT PRIMARY KEY,
                name TEXT DEFAULT 'Default',
                starting_capital REAL NOT NULL,
                current_cash REAL NOT NULL,
                risk_profile TEXT NOT NULL,
                investment_horizon TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_rebalanced_at TEXT
            );

            CREATE TABLE IF NOT EXISTS holdings (
                portfolio_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                shares REAL NOT NULL,
                avg_cost_basis REAL NOT NULL,
                asset_type TEXT NOT NULL DEFAULT 'stock',
                company_name TEXT,
                sector TEXT,
                geography TEXT DEFAULT 'us',
                acquired_at TEXT NOT NULL,
                PRIMARY KEY (portfolio_id, symbol),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
            );

            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                shares REAL NOT NULL,
                price REAL NOT NULL,
                total_value REAL NOT NULL,
                formula_score REAL,
                reason TEXT,
                status TEXT DEFAULT 'executed',
                trigger TEXT,
                proposed_at TEXT NOT NULL,
                executed_at TEXT,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                total_value REAL NOT NULL,
                cash_value REAL NOT NULL,
                holdings_value REAL NOT NULL,
                daily_return REAL DEFAULT 0,
                cumulative_return REAL DEFAULT 0,
                benchmark_return REAL DEFAULT 0,
                sharpe_ratio REAL DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                sector_allocation TEXT,
                geo_allocation TEXT,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
            );
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Portfolios
# ---------------------------------------------------------------------------


def create_portfolio(
    starting_capital: float,
    risk_profile: str,
    horizon: str,
    name: str | None = None,
) -> str:
    """Create a new portfolio and return its id."""
    portfolio_id = str(uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO portfolios
                (portfolio_id, name, starting_capital, current_cash,
                 risk_profile, investment_horizon, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                portfolio_id,
                name or "Default",
                starting_capital,
                starting_capital,
                risk_profile,
                horizon,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return portfolio_id


def get_portfolio(portfolio_id: str) -> dict | None:
    """Fetch a single portfolio by id, or None if not found."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM portfolios WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_portfolio(portfolio_id: str, **kwargs) -> None:
    """Update specified columns on a portfolio."""
    if not kwargs:
        return
    columns = ", ".join(f"{col} = ?" for col in kwargs)
    values = list(kwargs.values())
    values.append(portfolio_id)
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE portfolios SET {columns} WHERE portfolio_id = ?",
            values,
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------


def get_holdings(portfolio_id: str) -> list[dict]:
    """Return all holdings for a portfolio."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_holding(
    portfolio_id: str,
    symbol: str,
    shares: float,
    avg_cost_basis: float,
    asset_type: str = "stock",
    sector: str | None = None,
    geography: str = "us",
    company_name: str | None = None,
    acquired_at: str | None = None,
) -> None:
    """Insert or replace a holding row."""
    acquired_at = acquired_at or datetime.utcnow().isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO holdings
                (portfolio_id, symbol, shares, avg_cost_basis, asset_type,
                 company_name, sector, geography, acquired_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                portfolio_id,
                symbol,
                shares,
                avg_cost_basis,
                asset_type,
                company_name,
                sector,
                geography,
                acquired_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def delete_holding(portfolio_id: str, symbol: str) -> None:
    """Remove a holding from a portfolio."""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM holdings WHERE portfolio_id = ? AND symbol = ?",
            (portfolio_id, symbol),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


def save_trade(trade_dict: dict) -> None:
    """Insert a trade row from a dict."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO trades
                (trade_id, portfolio_id, symbol, action, shares, price,
                 total_value, formula_score, reason, status, trigger,
                 proposed_at, executed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_dict.get("trade_id", str(uuid4())),
                trade_dict["portfolio_id"],
                trade_dict["symbol"],
                trade_dict["action"],
                trade_dict["shares"],
                trade_dict["price"],
                trade_dict["total_value"],
                trade_dict.get("formula_score"),
                trade_dict.get("reason"),
                trade_dict.get("status", "executed"),
                trade_dict.get("trigger"),
                trade_dict.get("proposed_at", datetime.utcnow().isoformat()),
                trade_dict.get("executed_at"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_trades(
    portfolio_id: str,
    status: str | None = None,
) -> list[dict]:
    """Return trades for a portfolio, newest first. Optionally filter by status."""
    conn = get_connection()
    try:
        if status is not None:
            rows = conn.execute(
                "SELECT * FROM trades WHERE portfolio_id = ? AND status = ? "
                "ORDER BY proposed_at DESC",
                (portfolio_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades WHERE portfolio_id = ? "
                "ORDER BY proposed_at DESC",
                (portfolio_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def save_snapshot(snapshot_dict: dict) -> None:
    """Insert a portfolio snapshot. Dict values for allocation fields are JSON-serialized."""
    data = dict(snapshot_dict)
    data.setdefault("id", str(uuid4()))

    for field in ("sector_allocation", "geo_allocation"):
        val = data.get(field)
        if isinstance(val, dict):
            data[field] = json.dumps(val)

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO portfolio_snapshots
                (id, portfolio_id, snapshot_date, total_value, cash_value,
                 holdings_value, daily_return, cumulative_return,
                 benchmark_return, sharpe_ratio, max_drawdown,
                 sector_allocation, geo_allocation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["id"],
                data["portfolio_id"],
                data["snapshot_date"],
                data["total_value"],
                data["cash_value"],
                data["holdings_value"],
                data.get("daily_return", 0),
                data.get("cumulative_return", 0),
                data.get("benchmark_return", 0),
                data.get("sharpe_ratio", 0),
                data.get("max_drawdown", 0),
                data.get("sector_allocation"),
                data.get("geo_allocation"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_snapshots(
    portfolio_id: str,
    limit: int = 365,
) -> list[dict]:
    """Return snapshots newest-first, JSON-parsing allocation fields."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE portfolio_id = ? "
            "ORDER BY snapshot_date DESC LIMIT ?",
            (portfolio_id, limit),
        ).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        d = dict(row)
        for field in ("sector_allocation", "geo_allocation"):
            val = d.get(field)
            if val is not None:
                try:
                    d[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        results.append(d)
    return results
