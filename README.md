# financial-mcp-server

[![PyPI Downloads](https://static.pepy.tech/personalized-badge/financial-mcp-server?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/financial-mcp-server)

Give your AI agent real stock-market data. This is an [MCP](https://modelcontextprotocol.io)
server with **33 tools** for prices, fundamentals, SEC filings, macro data, futures
positioning, market-regime detection, and paper trading — usable by any MCP-compatible
LLM or agent (Claude, Cursor, and more).

**No API keys required** to get started. Data comes from yfinance, SEC EDGAR, CFTC,
Treasury.gov, and Google Trends.

---

## Quick start (2 minutes)

You connect this server to your AI app once, then just talk to your agent normally —
it calls the tools for you. There's nothing to run yourself.

### Step 1 — Install a launcher

You need **one** of these on your machine:

- **uv** (recommended — runs the server without installing it):
  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Windows (PowerShell)
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
- **or pip** (Python 3.10+ already installed):
  ```bash
  pip install financial-mcp-server
  ```

### Step 2 — Add it to your AI app

Pick your app below, paste the config, and restart the app. **That's it.**

<details open>
<summary><b>Claude Desktop</b></summary>

Open your config file (create it if it doesn't exist):

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add this, then fully quit and reopen Claude Desktop:

```json
{
  "mcpServers": {
    "financial-mcp": {
      "command": "uvx",
      "args": ["financial-mcp-server"]
    }
  }
}
```
</details>

<details>
<summary><b>Claude Code</b></summary>

One command:

```bash
claude mcp add financial-mcp -- uvx financial-mcp-server
```
</details>

<details>
<summary><b>Cursor</b></summary>

Create `.cursor/mcp.json` in your project (or `~/.cursor/mcp.json` for all projects):

```json
{
  "mcpServers": {
    "financial-mcp": {
      "command": "uvx",
      "args": ["financial-mcp-server"]
    }
  }
}
```
Then enable it in **Settings → MCP**.
</details>

<details>
<summary><b>Any other MCP client (Windsurf, custom agents, SDKs…)</b></summary>

Configure an MCP server that launches this command over **stdio**:

```
command: uvx
args:    ["financial-mcp-server"]
```

If you installed with pip instead of uv, the command is just `financial-mcp`
(no args). Most clients use the same `mcpServers` JSON shape shown above.
</details>

> **Using `pip` instead of `uv`?** Replace `"command": "uvx", "args": ["financial-mcp-server"]`
> with `"command": "financial-mcp"` everywhere above.

### Step 3 — Verify it's working

In your agent, ask:

> **"What's the current price of AAPL?"**

If it answers with a live price, you're connected. Other things to try:

> - "Analyze NVDA — fundamentals, momentum, and a score."
> - "What market regime are we in right now?"
> - "Show me recent SEC 10-K filings for Microsoft."
> - "Scan AAPL, MSFT, GOOGL, AMZN and rank them."

---

## What you can ask for (all 33 tools)

You don't call these directly — your agent picks the right one from your question.
This is just a reference for what's available.

### Analysis & Scoring
| Tool | What it does |
|------|-------------|
| `analyze_ticker` | Full analysis: fundamentals, momentum, composite score |
| `scan_universe` | Rank tickers by composite score |
| `score_ticker` | Composite score (0-100) with breakdown |
| `get_fundamentals` | PE, EV/EBITDA, P/B, dividend yield, market cap |
| `get_momentum` | 30d/90d momentum, volatility, relative strength |
| `get_price` | Current price |

### SEC EDGAR
| Tool | What it does |
|------|-------------|
| `get_sec_filings` | 10-K, 10-Q, 8-K filings for any public company |
| `get_insider_trades` | Insider buys/sells (Forms 3/4/5) |
| `search_sec_filings` | Full-text search across all SEC filings |

### Macro & Economic
| Tool | What it does |
|------|-------------|
| `get_economic_indicator` | Any FRED series (GDP, CPI, unemployment, etc.) |
| `get_yield_curve` | Treasury yield curve with inversion detection |
| `get_economic_snapshot` | Key indicators at a glance |
| `get_treasury_rates` | Average Treasury interest rates |
| `get_treasury_yield_curve` | Daily yield curve data (1mo-30yr) |
| `get_treasury_auctions` | Recent auction results |

### Futures & Positioning
| Tool | What it does |
|------|-------------|
| `get_futures_positioning` | CFTC COT data for any commodity/index |
| `get_smart_money_signal` | Bullish/bearish signal from commercial hedgers |

### Sentiment & Trends
| Tool | What it does |
|------|-------------|
| `get_search_trends` | Google Trends interest over time |
| `get_trending_searches` | Currently trending searches |

### Market Intelligence
| Tool | What it does |
|------|-------------|
| `detect_market_regime` | BULL / BEAR / SIDEWAYS / HIGH_VOLATILITY / CRASH |
| `get_regime_history` | Monthly regime classification |
| `get_vix_analysis` | VIX level, percentile, fear signal |
| `scan_anomalies` | Volume spikes, gaps, 52w extremes, divergences |
| `scan_volume_leaders` | Unusual volume detection |
| `scan_gap_movers` | Significant gap ups/downs at open |

### Portfolio & Paper Trading
| Tool | What it does |
|------|-------------|
| `create_portfolio` | Start a paper portfolio (capital, risk profile, horizon) |
| `analyze_portfolio` | Holdings, allocations, performance, and risk summary |
| `get_holdings` | Current positions with values |
| `get_trades` | Trade history (filter by status) |
| `execute_buy` | Buy shares at the current price |
| `execute_sell` | Sell shares at the current price |
| `run_rebalance` | Score a universe and execute buy/sell signals |
| `check_risk` | Stress score, scenario drawdowns, concentration |

Paper trades are saved to a local SQLite file (see `FINANCIAL_MCP_DB_PATH` below).

---

## Optional settings

Everything works out of the box. These are only if you want to customize:

| Environment variable | What it does | Default |
|----------------------|--------------|---------|
| `FRED_API_KEY` | Unlocks the FRED macro tools — [grab a free key](https://fred.stlouisfed.org/docs/api/api_key.html) | unset (FRED tools limited) |
| `FINANCIAL_MCP_DB_PATH` | Where paper-trading data is stored | `~/.financial-mcp/financial_mcp.db` |
| `FINANCIAL_MCP_TRANSPORT` | `stdio` (for AI apps) or `sse` (network server) | `stdio` |
| `FINANCIAL_MCP_CONFIG` | Path to a custom `config.yaml` | built-in defaults |

To set an env var in Claude Desktop / Cursor, add an `"env"` block to the config:

```json
{
  "mcpServers": {
    "financial-mcp": {
      "command": "uvx",
      "args": ["financial-mcp-server"],
      "env": { "FRED_API_KEY": "your-key-here" }
    }
  }
}
```

**Running as a standalone network server?** `financial-mcp --transport sse`.

---

## Troubleshooting

- **Agent doesn't see the tools** → fully restart the app after editing the config
  (Claude Desktop must be *quit*, not just closed).
- **`uvx: command not found`** → install uv (Step 1), or switch to the pip method
  (`pip install financial-mcp-server`, then use `"command": "financial-mcp"`).
- **Want to confirm the package runs at all?** → `uvx financial-mcp-server --help`.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for what changed in each release.

## License

MIT
