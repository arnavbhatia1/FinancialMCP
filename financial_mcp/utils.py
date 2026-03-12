"""Shared utility functions and constants for the FinancialMCP package."""

TRADING_DAYS_PER_YEAR = 252


def safe_round(value: float | None, decimals: int = 4) -> float | None:
    """Round *value* if it is not None."""
    if value is None:
        return None
    return round(float(value), decimals)
