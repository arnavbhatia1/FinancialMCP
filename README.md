# financial-mcp

MCP server for AI-powered stock analysis and paper trading. Exposes 14 tools over SSE for any MCP-compatible AI agent.

No API keys required. All market data comes from yfinance (free). Portfolio state is stored locally in SQLite.

## Install

```bash
pip install financial-mcp
```

## Quick Start

```bash
financial-mcp
```

Server starts on `http://0.0.0.0:8520/sse`.

### Claude Desktop / Claude Code

Add to your MCP config:

```json
{
  "mcpServers": {
    "financial-mcp": {
      "command": "uvx",
      "args": ["financial-mcp"]
    }
  }
}
```

## Tools

### Orchestration (4)

| Tool | Description |
|------|-------------|
| `analyze_ticker` | Full analysis: fundamentals + momentum + composite score |
| `analyze_portfolio` | Portfolio summary with holdings, allocations, performance, risk |
| `run_rebalance` | Score universe, generate buy/sell signals, execute trades |
| `scan_universe` | Score a list of tickers and rank by composite score |

### Primitives (10)

| Tool | Description |
|------|-------------|
| `create_portfolio` | Create a new paper trading portfolio |
| `get_fundamentals` | PE, EV/EBITDA, P/B, dividend yield, market cap, sector |
| `get_momentum` | 30d/90d momentum, volatility, relative strength, drawdown |
| `get_price` | Current price for a ticker |
| `score_ticker` | Composite score (0-100) with component breakdown |
| `get_holdings` | Current portfolio holdings |
| `get_trades` | Trade history with optional status filter |
| `execute_buy` | Buy shares via paper broker |
| `execute_sell` | Sell shares via paper broker |
| `check_risk` | Stress score, scenario drawdowns, sector/geo allocation |

## Scoring Formula

3-signal composite (optional 4th with sentiment):

- **Valuation (40%):** PE, EV/EBITDA, P/B, dividend yield relative to sector medians
- **Momentum (35%):** 30d/90d price momentum, relative strength vs SPY, volatility
- **Risk penalty (25%):** Sector concentration, geographic concentration, max drawdown

Scores range 0-100. Buy threshold: 65. Sell threshold: 35.

## Configuration

Default config is built-in. Override by placing a `config.yaml` in your working directory:

```yaml
server:
  host: "0.0.0.0"
  port: 8520

scoring:
  weights:
    valuation: 0.40
    momentum: 0.35
    risk: 0.25
  buy_threshold: 65
  sell_threshold: 35

position_limits:
  moderate:
    max_position: 0.08
    max_sector: 0.30
    min_cash: 0.10
```

## Example Usage

Once connected, an AI agent can:

```
> Create a $100K moderate portfolio
> Scan AAPL, MSFT, GOOGL, NVDA, META, AMZN, JPM, V
> Buy the top 3 scored tickers
> Check portfolio risk
> Run a full rebalance
```

## License

MIT
