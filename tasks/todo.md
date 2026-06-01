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

## Follow-ups found during this release (not fixed here)
- **Scoring quirk:** a ticker with no fundamentals/momentum and no portfolio
  context scores **100**, not a neutral 50 — `compute_risk_penalty` returns 0
  (inverted to 100) and is never `None`, so the "all signals None -> 50" branch
  in `score_ticker` is effectively dead. Decide intended behavior before changing
  (would alter scoring semantics for every no-data ticker).

## Out of scope (future — Full tier)
TTL cache for external APIs, new tools (options/IV, earnings, analyst ratings),
MCP resources/prompt templates.
