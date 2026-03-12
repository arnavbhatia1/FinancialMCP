"""FinancialMCP — MCP server for AI-powered stock analysis and paper trading."""

try:
    from importlib.metadata import version
    __version__ = version("financial-mcp-server")
except Exception:
    __version__ = "0.0.0"
