# FinancialMCP — Design Spec

## Overview

A standalone, lightweight MCP server that exposes AI-powered stock analysis and paper trading tools over SSE. Built with Python, backed by yfinance for market data and SQLite for state. No API keys required. Installable via pip.

**Repo:** `C:\Users\abhat\Personal\FinancialMCP` (published to GitHub)

**Target users:** AI agents (Claude, custom agents) that need financial analysis and paper trading capabilities via MCP.

---

## 1. Tool Inventory

### High-Level Orchestration (4 tools)

| Tool | Inputs | Returns | Maps to |
|------|--------|---------|---------|
| `analyze_ticker` | `symbol: str` | Fundamentals + momentum + score + component breakdown | `engine.score_ticker()` + `market_data.*` |
| `analyze_portfolio` | `portfolio_id: str` | Holdings, allocations, performance metrics, risk summary | `portfolio.get_summary()` + `risk.compute_stress_score()` |
| `run_rebalance` | `portfolio_id: str, trigger?: str, symbols?: list[str]` | Scored universe, buy/sell signals, executed trades, risk report. Trades are auto-executed (no review gate). If `symbols` omitted, scores current holdings + common large-caps. | Full rebalance cycle |
| `scan_universe` | `symbols: list[str]` | Ranked list of scored tickers. Internally calls `get_batch_fundamentals()` and `get_momentum_signals()` per symbol before scoring. | `engine.score_universe()` |

### Fine-Grained Primitives (10 tools)

| Tool | Inputs | Returns | Maps to |
|------|--------|---------|---------|
| `create_portfolio` | `starting_capital: float, risk_profile: str, investment_horizon: str, name?: str` | `{portfolio_id, starting_capital, risk_profile, horizon}` | `portfolio.create_portfolio()` (validates then delegates to `db.create_portfolio()`) |
| `get_fundamentals` | `symbol: str` | PE, EV/EBITDA, P/B, div yield, market cap, sector | `market_data.get_fundamentals()` |
| `get_momentum` | `symbol: str` | 30d/90d momentum, volatility, RS, max drawdown | `market_data.get_momentum_signals()` |
| `get_price` | `symbol: str` | Current price (float) | `market_data.get_current_price()` |
| `score_ticker` | `symbol: str, sentiment?: dict` | Score 0-100 + component breakdown | `engine.score_ticker()` |
| `get_holdings` | `portfolio_id: str` | List of current holdings with values | `db.get_holdings()` |
| `get_trades` | `portfolio_id: str, status?: str` | Trade history | `db.get_trades()` |
| `execute_buy` | `portfolio_id: str, symbol: str, shares: int` | TradeResult (success, price, cost) | `broker.PaperBroker.execute_buy()` |
| `execute_sell` | `portfolio_id: str, symbol: str, shares: int` | TradeResult (success, price, proceeds) | `broker.PaperBroker.execute_sell()` |
| `check_risk` | `portfolio_id: str` | `{stress_score, scenario_drawdowns, vulnerable_sectors, sector_allocation, geo_allocation}`. Position-limit checks happen inside `execute_buy`/`execute_sell`. | `risk.compute_stress_score()` + `risk.get_sector_allocation()` + `risk.get_geo_allocation()` |

**Total: 14 tools** (4 orchestration + 10 primitives).

---

## 2. Architecture

```
Agent (MCP Client)
    │
    │  SSE (http://localhost:8520/sse)
    ▼
┌─────────────────────────────┐
│  server.py (MCP Server)     │
│  - Tool registration        │
│  - Input validation         │
│  - JSON responses           │
├─────────────────────────────┤
│  engine.py    │ broker.py   │
│  market_data.py│ risk.py    │
│  portfolio.py │ db.py       │
└───────┬─────────────────────┘
        │
   ┌────┴────┐
   │ SQLite  │  yfinance
   │ (local) │  (free API)
   └─────────┘
```

**Transport:** SSE over HTTP on configurable port (default 8520).

**State:** Single SQLite file at `data/financial_mcp.db`. Created on first run.

**No required API keys.** All data comes from yfinance (free).

---

## 3. Module Design

### `financial_mcp/db.py` — Storage Layer

SQLite with 4 tables:

```sql
portfolios (
    portfolio_id TEXT PRIMARY KEY,
    name TEXT DEFAULT 'Default',
    starting_capital REAL NOT NULL,
    current_cash REAL NOT NULL,
    risk_profile TEXT NOT NULL,        -- conservative | moderate | aggressive
    investment_horizon TEXT NOT NULL,   -- short | medium | long
    created_at TEXT NOT NULL,
    last_rebalanced_at TEXT
)

holdings (
    portfolio_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    shares REAL NOT NULL,
    avg_cost_basis REAL NOT NULL,
    asset_type TEXT NOT NULL DEFAULT 'stock',  -- stock | etf
    company_name TEXT,
    sector TEXT,
    geography TEXT DEFAULT 'us',
    acquired_at TEXT NOT NULL,
    PRIMARY KEY (portfolio_id, symbol),
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
)

trades (
    trade_id TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,               -- buy | sell
    shares REAL NOT NULL,
    price REAL NOT NULL,
    total_value REAL NOT NULL,
    formula_score REAL,
    reason TEXT,
    status TEXT DEFAULT 'executed',     -- proposed | executed | rejected
    trigger TEXT,                        -- manual | rebalance | agent
    proposed_at TEXT NOT NULL,
    executed_at TEXT,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
)

portfolio_snapshots (
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
    sector_allocation TEXT,             -- JSON
    geo_allocation TEXT,                -- JSON
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
)
```

Functions:
- `init_db()` — create tables if not exist
- `create_portfolio(starting_capital, risk_profile, horizon, name?)` → portfolio_id
- `get_portfolio(portfolio_id)` → dict | None
- `get_holdings(portfolio_id)` → list[dict]
- `upsert_holding(portfolio_id, symbol, shares, avg_cost_basis, asset_type, sector?, geo?, company_name?, acquired_at?)`
- `delete_holding(portfolio_id, symbol)`
- `save_trade(trade_dict)`
- `get_trades(portfolio_id, status?)` → list[dict]
- `update_portfolio(portfolio_id, **kwargs)`
- `save_snapshot(snapshot_dict)`
- `get_snapshots(portfolio_id, limit=365)` → list[dict]

### `financial_mcp/market_data.py` — yfinance Wrapper

- `get_fundamentals(symbol)` → `{pe_ratio, ev_to_ebitda, price_to_book, dividend_yield, market_cap, sector, industry}` with None for missing fields
- `get_current_price(symbol)` → float | None
- `get_momentum_signals(symbol)` → `{price_momentum_30d, price_momentum_90d, volatility, relative_strength, max_drawdown}`
- `get_batch_fundamentals(symbols)` → dict[symbol → fundamentals]
- `get_sector_medians(batch_fundamentals)` → dict[sector → {median_pe, median_ev_ebitda}]

All functions return None/empty gracefully on yfinance failure. No exceptions leak to the MCP layer.

### `financial_mcp/engine.py` — Scoring Formula

Simplified 3-signal composite (no sentiment by default, optional if provided):

**Signals:**
- **Valuation (40%):** PE, EV/EBITDA, P/B, dividend yield — each scored 0-100 relative to sector medians, weight-redistributed when missing
- **Momentum (35%):** 30d/90d price momentum, relative strength, volatility — percentile-ranked across the input universe
- **Risk penalty (25%):** Sector concentration, geographic concentration, max drawdown

**Optional sentiment (if provided):** Redistributes weights to include sentiment at 25%, reducing others proportionally.

**Output:** Score 0-100 per ticker with component breakdown.

Functions:
- `score_ticker(symbol, fundamentals, momentum, all_momentum, sector_medians, holdings=None, portfolio_value=0, risk_profile='moderate', config=None, sentiment=None)` → `{score, valuation, momentum, risk_penalty, sentiment?}`
- `score_universe(symbols, holdings=None, portfolio_value=0, risk_profile='moderate', config=None)` → sorted list of score dicts. Internally calls `get_batch_fundamentals(symbols)` and `get_momentum_signals()` per symbol, computes `sector_medians`, then scores each ticker.

### `financial_mcp/risk.py` — Risk Assessment

- `SECTOR_SENSITIVITY` — 13 sectors × 3 recession scenarios (2008, 2020, 2022)
- `check_position_limits(symbol, proposed_value, holdings, portfolio_value, risk_profile, config)` → `{allowed, violations}`
- `compute_stress_score(holdings, portfolio_value, config)` → `{stress_score, scenario_drawdowns, vulnerable_sectors}`
- `get_sector_allocation(holdings, portfolio_value)` → dict
- `get_geo_allocation(holdings, portfolio_value)` → dict

### `financial_mcp/broker.py` — Paper Broker

SQLite-backed paper trading:

- `PaperBroker(portfolio_id)` — constructor loads portfolio from DB
- `execute_buy(symbol, shares)` → `{success, symbol, shares, price, total_cost, error?}`
- `execute_sell(symbol, shares)` → `{success, symbol, shares, price, total_proceeds, error?}`

Validates: sufficient cash (buys), sufficient shares (sells), valid price from yfinance. Updates holdings with weighted average cost basis. Auto-detects ETFs vs stocks for `asset_type`.

### `financial_mcp/portfolio.py` — Portfolio Operations

- `create_portfolio(starting_capital, risk_profile, horizon, name?)` → portfolio_id. Validates inputs (capital range, valid profile/horizon) then delegates to `db.create_portfolio()`.
- `get_summary(portfolio_id)` → `{portfolio, holdings, total_value, allocations, daily_change}`
- `compute_performance(portfolio_id)` → `{cumulative_return, daily_return, sharpe_ratio, max_drawdown, benchmark_return}`
- `take_snapshot(portfolio_id)` → snapshot dict

### `financial_mcp/server.py` — MCP Server

Registers all 14 tools with the `mcp` SDK. Each tool:
1. Validates inputs via JSON Schema (handled by MCP SDK)
2. Calls the appropriate module function
3. Returns structured JSON as `TextContent`
4. Catches all exceptions and returns error messages (never crashes the server)

The server calls `db.init_db()` on startup. Config loaded once via `yaml.safe_load()` and passed to tool functions.

---

## 4. Configuration

`config.yaml` at repo root:

```yaml
server:
  host: "0.0.0.0"
  port: 8520
  name: "financial-mcp"

database:
  path: "data/financial_mcp.db"

scoring:
  weights:
    valuation: 0.40
    momentum: 0.35
    risk: 0.25
  buy_threshold: 65
  sell_threshold: 35

position_limits:
  conservative:
    max_position: 0.05
    max_sector: 0.20
    min_cash: 0.15
  moderate:
    max_position: 0.08
    max_sector: 0.30
    min_cash: 0.10
  aggressive:
    max_position: 0.12
    max_sector: 0.40
    min_cash: 0.05

stress_thresholds:
  conservative: { warning: 0.20, action: 0.25 }
  moderate: { warning: 0.28, action: 0.33 }
  aggressive: { warning: 0.35, action: 0.40 }
```

Loaded once at server startup via `yaml.safe_load()`. Tool functions receive config as a parameter.

---

## 5. Packaging

`pyproject.toml` for pip installability:

```toml
[project]
name = "financial-mcp"
version = "0.1.0"
description = "MCP server for AI-powered stock analysis and paper trading"
requires-python = ">=3.10"
dependencies = [
    "mcp[cli]>=1.0.0",
    "yfinance>=0.2.31",
    "pyyaml>=6.0",
]

[project.scripts]
financial-mcp = "financial_mcp.server:main"
```

**Install:** `pip install .` or `pip install git+https://github.com/user/FinancialMCP.git`

**Run:** `financial-mcp` (from installed script) or `python scripts/run_server.py`

---

## 6. Error Handling

Every tool wraps its logic in try/except and returns structured errors:

```json
{"error": "Symbol XXXXX not found", "tool": "get_fundamentals"}
```

The MCP server never crashes on bad input. yfinance failures return None/empty results, not exceptions.

---

## 7. What's NOT Included

- No sentiment ingestion (no Reddit/Stocktwits/RSS scraping) — sentiment is accepted as optional input to scoring tools
- No Claude API calls / AI review gate — trades auto-execute without review
- No Streamlit UI
- No ML/training pipeline
- No authentication/users — portfolios are identified by ID only, all operate in autopilot mode
- No real brokerage integration — paper trading only

These are intentional scope cuts to keep the server lightweight and dependency-free.
