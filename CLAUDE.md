# CLAUDE.md — financial-mcp-server

MCP server exposing 33 stock-market / macro / paper-trading tools to any
MCP-compatible AI agent. Published to PyPI as `financial-mcp-server`.

## Tech stack
- Python >= 3.10
- `mcp[cli]` (FastMCP) — tool server
- Data sources: yfinance, SEC EDGAR, FRED, CFTC, Treasury.gov, Google Trends (pytrends)
- SQLite (stdlib) for paper-trading state
- setuptools + setuptools-scm (version derived from git tags)

## Commands
- Run (stdio, default): `financial-mcp`  ·  SSE: `financial-mcp --transport sse`
- Install for dev: `pip install -e ".[test]"`
- Test (offline, the publish gate): `pytest -q`
- Test (live data, opt-in): `pytest -m network`
- Build: `python -m build`

## Transport
Defaults to **stdio** (uvx / Claude Desktop / Claude Code). SSE is opt-in via
`--transport sse` or `FINANCIAL_MCP_TRANSPORT=sse`. All logging goes to **stderr** —
never print to stdout; it is the JSON-RPC channel.

## Config / env
No config file required — `DEFAULT_CONFIG` in `server.py` covers everything.
Optional: `FRED_API_KEY`, `FINANCIAL_MCP_DB_PATH` (default `~/.financial-mcp/financial_mcp.db`),
`FINANCIAL_MCP_CONFIG`, `FINANCIAL_MCP_TRANSPORT`. Never default the DB under site-packages.

## Structure
- `financial_mcp/server.py` — FastMCP app + all `@mcp.tool()` definitions (the registry)
- `financial_mcp/engine.py` — composite scoring (valuation/momentum/risk); weights are
  code constants, not config
- `financial_mcp/market_data.py` — yfinance access
- `financial_mcp/{sec_edgar,fred,cftc,trends,treasury,regime,anomaly}.py` — data-source modules
- `financial_mcp/{db,portfolio,broker,risk}.py` — paper-trading layer
- `tests/` — offline by default; `test_live.py` holds opt-in `@network` tests.
  Mirror the registered tool count in `test_server.py`

## Conventions
- Every tool wraps its body in try/except and returns JSON via `_json` / `_error`.
- Keep `README.md` tool tables and `EXPECTED_TOOL_COUNT` in `tests/test_server.py` in
  sync with the registered tools — update all three in the same commit.

## Release
Push to `master` auto-bumps the patch tag and publishes to PyPI via Trusted Publishing
(`.github/workflows/publish.yml`), **gated on `pytest`**. Docs-only commits
(`**/*.md`, `docs/**`, `tasks/**`, `LICENSE`) are skipped via `paths-ignore`.
