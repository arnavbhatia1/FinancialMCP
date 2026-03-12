# financial-mcp-server

MCP server for AI-powered stock analysis and paper trading. 14 tools for any MCP-compatible AI agent.

No API keys required. Market data from yfinance. Portfolio state in local SQLite.

## Tools

| Tool | What it does |
|------|-------------|
| `analyze_ticker` | Full analysis: fundamentals, momentum, composite score |
| `analyze_portfolio` | Holdings, allocations, performance, risk summary |
| `run_rebalance` | Score universe, generate signals, execute trades |
| `scan_universe` | Rank tickers by composite score |
| `create_portfolio` | Create a paper trading portfolio |
| `get_fundamentals` | PE, EV/EBITDA, P/B, dividend yield, market cap |
| `get_momentum` | 30d/90d momentum, volatility, relative strength |
| `get_price` | Current price |
| `score_ticker` | Composite score (0-100) with breakdown |
| `get_holdings` | Portfolio holdings |
| `get_trades` | Trade history |
| `execute_buy` | Buy shares (paper) |
| `execute_sell` | Sell shares (paper) |
| `check_risk` | Stress score, scenario drawdowns, allocations |

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
