# financial-mcp-server

MCP server for AI-powered stock analysis and paper trading. 33 tools for any MCP-compatible AI agent.

No API keys required (FRED key optional). Market data from yfinance, SEC EDGAR, CFTC, Treasury.gov, and Google Trends. Portfolio state in local SQLite.

## Tools

### Analysis & Scoring
| Tool | What it does |
|------|-------------|
| `analyze_ticker` | Full analysis: fundamentals, momentum, composite score |
| `analyze_portfolio` | Holdings, allocations, performance, risk summary |
| `scan_universe` | Rank tickers by composite score |
| `score_ticker` | Composite score (0-100) with breakdown |
| `get_fundamentals` | PE, EV/EBITDA, P/B, dividend yield, market cap |
| `get_momentum` | 30d/90d momentum, volatility, relative strength |
| `get_price` | Current price |

### Paper Trading
| Tool | What it does |
|------|-------------|
| `create_portfolio` | Create a paper trading portfolio |
| `run_rebalance` | Score universe, generate signals, execute trades |
| `execute_buy` | Buy shares (paper) |
| `execute_sell` | Sell shares (paper) |
| `get_holdings` | Portfolio holdings |
| `get_trades` | Trade history |
| `check_risk` | Stress score, scenario drawdowns, allocations |

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

## Install

**pip:**
```bash
pip install financial-mcp-server
financial-mcp
```

**uvx (no install):**
```bash
uvx financial-mcp-server
```

**Claude Desktop** (`claude_desktop_config.json`):
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

**Claude Code:**
```bash
claude mcp add financial-mcp -- uvx financial-mcp-server
```

## License

MIT
