"""Tests for config merge and DB-path resolution (installed-mode correctness)."""

import os

from financial_mcp import server


def test_default_config_is_complete():
    cfg = server.DEFAULT_CONFIG
    assert "server" in cfg and "position_limits" in cfg
    for profile in ("conservative", "moderate", "aggressive"):
        assert profile in cfg["position_limits"]


def test_db_path_prefers_env(monkeypatch):
    monkeypatch.setenv("FINANCIAL_MCP_DB_PATH", "/tmp/custom_fmcp.db")
    assert server._resolve_db_path({}) == "/tmp/custom_fmcp.db"


def test_db_path_expands_user(monkeypatch):
    monkeypatch.setenv("FINANCIAL_MCP_DB_PATH", "~/my.db")
    resolved = server._resolve_db_path({})
    assert "~" not in resolved


def test_db_path_falls_back_to_home_not_site_packages(monkeypatch):
    monkeypatch.delenv("FINANCIAL_MCP_DB_PATH", raising=False)
    resolved = server._resolve_db_path({"database": {"path": "data/x.db"}})
    # A relative config path must NOT be used verbatim (would land in site-packages).
    assert resolved.endswith(os.path.join(".financial-mcp", "financial_mcp.db"))


def test_db_path_honors_absolute_config(monkeypatch):
    monkeypatch.delenv("FINANCIAL_MCP_DB_PATH", raising=False)
    # abspath gives a platform-correct absolute path (drive letter on Windows).
    abs_path = os.path.abspath(os.path.join(os.sep, "data", "fmcp.db"))
    assert server._resolve_db_path({"database": {"path": abs_path}}) == abs_path
