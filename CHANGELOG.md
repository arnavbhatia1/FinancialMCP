# Changelog

All notable changes to **financial-mcp-server** are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [0.1.11] - 2026-06-01

### Added
- `scan_universe` now returns the latest `price` for each scored symbol, fetched
  in a single batched `yf.download` (`market_data.get_batch_prices`). Callers
  (e.g. an autonomous trading bot) can rank a universe *and* size positions from
  one tool call instead of issuing a separate price lookup per ticker.

## [0.1.10] - 2026-06-01

### Fixed
- **Market data now works from cloud / datacenter hosts** (Streamlit Cloud, AWS,
  GCP). Yahoo blocks requests with a non-browser TLS fingerprint, so `yfinance`
  worked on a laptop but returned empty prices/fundamentals/momentum on cloud.
  All yfinance access now goes through a `curl_cffi` session impersonating Chrome
  (`market_data`, `anomaly`, `regime`), which presents a real browser fingerprint.
- `get_current_price` now reads from price history first (reachable on cloud even
  when the `.info`/quote endpoint is throttled) before falling back to `.info`.

### Added
- Short in-process TTL cache for `.info` / history lookups, cutting yfinance call
  volume (and thus rate-limiting) when the same tickers are scanned repeatedly.
- `curl_cffi` is now an explicit dependency.

## [0.1.9] - 2026-06-01

### Fixed
- Corrected the PyPI project links (Homepage / Repository / Issues), which
  pointed at a non-existent `arnavbhat1/financial-mcp` repo and 404'd. They now
  point to `arnavbhatia1/FinancialMCP`.

### Added
- `Changelog` project link, so the changelog is reachable from the PyPI sidebar.
- Automated release notes: every PyPI publish now also creates a matching
  GitHub Release populated with that version's changelog entry.

## [0.1.8] - 2026-06-01

### Fixed
- **Treasury interest-rate tool** (`get_treasury_rates`) returned no data — it
  filtered on `security_type_desc` (which only holds *Marketable* /
  *Non-marketable*) instead of `security_desc` (*Treasury Bills/Notes/Bonds/
  TIPS*). Now returns rates, and the fetch window widens for small day counts.
- **Treasury daily yield curve** (`get_treasury_yield_curve`) returned no data —
  the legacy `data.treasury.gov` OData feed was retired and now serves HTML.
  Switched to Treasury's daily-yield-curve XML feed (parsed with the standard
  library; no new dependencies).
- **Scoring**: a ticker with no fundamentals/momentum/sentiment now scores a
  neutral **50** instead of a misleading **100** (risk is a penalty, not
  evidence of value, so a zero penalty no longer inverts into a top score).

### Added
- Opt-in live data tests (`pytest -m network`) covering price, Treasury rates,
  Treasury yield curve, market regime, and SEC EDGAR. CI runs them
  **non-blocking**, so upstream API outages surface without blocking a release.
- README rewritten as a step-by-step setup guide (quick start, per-app config
  for Claude Desktop / Claude Code / Cursor / generic MCP, troubleshooting).

### Removed
- Dead code from the retired Treasury OData path.

## [0.1.7] - 2026-06-01

### Fixed
- **stdio transport is now the default** — the entry point hardcoded SSE, so the
  documented `uvx` / Claude Desktop / Claude Code integration never responded.
  Use `--transport sse` (or `FINANCIAL_MCP_TRANSPORT=sse`) for the network
  server. All logging goes to stderr so it can't corrupt the stdio channel.
- **Works when pip-installed** — config now falls back to built-in defaults (no
  `config.yaml` required), and the SQLite database resolves to a writable path
  (`~/.financial-mcp/`, overridable via `FINANCIAL_MCP_DB_PATH`) instead of a
  read-only location under `site-packages`. Database init is guarded so the
  read-only market/macro tools still load even if the DB path is unwritable.

### Added
- Configuration via environment variables: `FRED_API_KEY`,
  `FINANCIAL_MCP_DB_PATH`, `FINANCIAL_MCP_CONFIG`, `FINANCIAL_MCP_TRANSPORT`.
- Offline test suite plus a CI gate: tests must pass before publishing, and
  docs-only pushes no longer consume a version number.
- Documented all 33 tools (the 8 portfolio / paper-trading tools were missing).

### Removed
- Broken packaging reference to a `config.yaml` outside the package.
