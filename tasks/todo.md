# v0.1.7 — Robustness release

Goal: make the pip-installed package actually work for its advertised use (Claude
Desktop / Claude Code via stdio), fix installed-mode config/DB breakage, sync docs,
and add a test gate so bad commits don't auto-publish.

## Tasks

- [ ] **1. stdio transport (the critical fix)** — `server.py:main()`
  - Default to stdio (what `uvx` / Claude Desktop / Claude Code use)
  - `--transport {stdio,sse}` CLI flag + `FINANCIAL_MCP_TRANSPORT` env override
  - Keep all logging on stderr (stdout is the JSON-RPC channel)
- [ ] **2. Config + DB path work when pip-installed** — `server.py`
  - Embed `DEFAULT_CONFIG`; merge file/env on top so installed users always work
  - DB path: `FINANCIAL_MCP_DB_PATH` env > absolute config path > `~/.financial-mcp/financial_mcp.db`
  - Never write under `site-packages`; guard `init_db()` so import can't hard-crash
  - Config file lookup honors `FINANCIAL_MCP_CONFIG` env
- [ ] **3. pyproject.toml**
  - Remove broken `package-data = ["../config.yaml"]` (file is outside the package)
  - Add `[project.optional-dependencies] test = ["pytest"]`
- [ ] **4. README sync**
  - "24 tools" -> "33 tools"; add the 8 Portfolio & Paper Trading tools
  - Document transport (stdio default, `--transport sse`) + env vars (FRED_API_KEY, FINANCIAL_MCP_DB_PATH)
- [ ] **5. tests/** (offline only — no network in CI)
  - engine: normalize / percentile_rank / score_ticker on synthetic data
  - config + DB-path resolution from env
  - server imports cleanly and registers all 33 tools
- [ ] **6. CI** — `.github/workflows/publish.yml`
  - `paths-ignore` docs (`**/*.md`, `docs/**`, `LICENSE`) so docs commits don't burn a version
  - Run pytest before build; failure blocks publish
- [ ] **7. Add project `CLAUDE.md`** (repo currently has none)
- [ ] **8. Verify**: `pytest` green locally + `python -m build` succeeds
- [ ] **9. Commit + push to master** (triggers auto-publish of v0.1.7) — only after user OK

## v0.1.8 — data-layer fixes (done)
- [x] **#1 Treasury rates**: filtered on `security_type_desc` (Marketable/Non-marketable)
  instead of `security_desc` (Treasury Bills/Notes/...) -> always empty. Fixed +
  widened page size for small `days`. Verified live (12 rows).
- [x] **#2 Treasury daily yield curve**: legacy `data.treasury.gov` OData feed was
  retired (now returns HTML). Switched to Treasury's daily-yield-curve XML feed,
  parsed with stdlib ElementTree. Verified live (real 1mo-30yr yields).
- [skip] **#3 FRED / Trends**: require the user's own API key — not validated here.
- [x] **#4 Scoring quirk**: no-data ticker now returns neutral 50 (risk is a penalty,
  not evidence of value), instead of 100 from an inverted zero penalty.
- [x] **#5 Live tests**: `tests/test_live.py` (`@network`), opt-in via `pytest -m network`;
  CI runs them non-blocking so data regressions are visible without gating releases on
  third-party API uptime.

## Out of scope (future — Full tier)
TTL cache for external APIs, new tools (options/IV, earnings, analyst ratings),
MCP resources/prompt templates.
