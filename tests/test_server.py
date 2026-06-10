"""Smoke tests: the server imports and registers every tool."""

from financial_mcp import server

# Keep in sync with the tools documented in README.md.
EXPECTED_TOOL_COUNT = 42

EXPECTED_SAMPLE = {
    "analyze_ticker", "scan_universe", "get_price",
    "get_sec_filings", "get_economic_indicator", "get_futures_positioning",
    "get_search_trends", "get_treasury_rates", "detect_market_regime",
    "scan_anomalies", "create_portfolio", "execute_buy", "run_rebalance",
    "get_price_history", "get_technical_indicators", "get_sector_performance",
    "get_ticker_news", "get_earnings_info", "get_analyst_ratings",
    "get_options_snapshot", "get_market_brief", "search_fred_series",
}


def _tool_names():
    tools = server.mcp._tool_manager.list_tools()
    return {t.name for t in tools}


def test_all_tools_registered():
    names = _tool_names()
    assert len(names) == EXPECTED_TOOL_COUNT, (
        f"Expected {EXPECTED_TOOL_COUNT} tools, found {len(names)}: {sorted(names)}"
    )


def test_known_tools_present():
    names = _tool_names()
    missing = EXPECTED_SAMPLE - names
    assert not missing, f"Missing expected tools: {missing}"


def test_default_transport_is_stdio():
    # The advertised uvx / Claude Desktop integration depends on stdio being
    # the default and SSE remaining available as an opt-in.
    import inspect

    src = inspect.getsource(server.main)
    assert 'FINANCIAL_MCP_TRANSPORT' in src
    assert '"stdio"' in src
    assert 'transport="sse"' in src
