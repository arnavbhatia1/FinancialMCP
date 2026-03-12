"""FRED (Federal Reserve Economic Data) client for macroeconomic indicators.

Every public function catches exceptions internally and returns None or an
empty container -- callers never need to handle FRED API errors.

Requires a free API key set via the FRED_API_KEY environment variable.
Get one at https://fred.stlouisfed.org/docs/api/api_key.html
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.stlouisfed.org/fred"

_api_key = os.environ.get("FRED_API_KEY", "")

_NO_KEY_ERROR = {
    "error": (
        "FRED_API_KEY not set. "
        "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html"
    ),
}

# Yield curve maturities: series_id -> human-readable label.
_YIELD_CURVE_SERIES = {
    "DGS1MO": "1mo",
    "DGS3MO": "3mo",
    "DGS6MO": "6mo",
    "DGS1":   "1yr",
    "DGS2":   "2yr",
    "DGS3":   "3yr",
    "DGS5":   "5yr",
    "DGS7":   "7yr",
    "DGS10":  "10yr",
    "DGS20":  "20yr",
    "DGS30":  "30yr",
}

# Economic snapshot indicators: key -> (series_id, display_name).
_SNAPSHOT_SERIES = {
    "gdp_growth":    ("GDP",              "GDP Growth (annualized)"),
    "cpi":           ("CPIAUCSL",         "CPI (all items)"),
    "unemployment":  ("UNRATE",           "Unemployment Rate"),
    "fed_funds":     ("FEDFUNDS",         "Federal Funds Rate"),
    "vix":           ("VIXCLS",           "VIX"),
    "credit_spread": ("BAMLH0A0HYM2",    "High Yield OAS"),
    "yield_spread":  ("T10Y2Y",          "10yr-2yr Spread"),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_api_key() -> bool:
    """Return True if the API key is available."""
    return bool(_api_key)


def _get(endpoint: str, params: dict) -> dict | None:
    """Issue a GET request to FRED and return the parsed JSON, or None."""
    params["api_key"] = _api_key
    params["file_type"] = "json"
    url = f"{BASE_URL}/{endpoint}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        logger.exception("FRED request failed: %s", endpoint)
        return None


def _fetch_series_info(series_id: str) -> dict:
    """Fetch title and units for a series. Returns {} on failure."""
    data = _get("series", {"series_id": series_id})
    if data is None:
        return {}
    try:
        series = data["seriess"][0]
        return {
            "title": series.get("title", ""),
            "units": series.get("units", ""),
        }
    except (KeyError, IndexError):
        logger.warning("Could not parse series info for %s", series_id)
        return {}


def _latest_value(series_id: str) -> dict | None:
    """Fetch the most recent observation for *series_id*.

    Returns ``{"value": float_or_str, "date": str}`` or None.
    """
    data = _get(
        "series/observations",
        {"series_id": series_id, "sort_order": "desc", "limit": 1},
    )
    if data is None:
        return None
    try:
        obs = data["observations"][0]
        raw_value = obs["value"]
        # FRED uses "." for missing/unavailable data points.
        value = float(raw_value) if raw_value != "." else None
        return {"value": value, "date": obs["date"]}
    except (KeyError, IndexError, ValueError):
        logger.warning("Could not parse latest value for %s", series_id)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_series(
    series_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> dict | None:
    """Fetch observations for a FRED series.

    Parameters
    ----------
    series_id:
        FRED series identifier (e.g. ``"GDP"``, ``"UNRATE"``).
    start_date, end_date:
        Optional ISO date strings (``"YYYY-MM-DD"``) to bound the query.
    limit:
        Maximum number of observations to return (most recent first).

    Returns
    -------
    dict | None
        ``{series_id, title, units, observations: [{date, value}]}``
        or the no-key error dict, or None on failure.
    """
    if not _check_api_key():
        return dict(_NO_KEY_ERROR)

    try:
        # Observations -------------------------------------------------------
        params: dict = {
            "series_id": series_id,
            "sort_order": "desc",
            "limit": limit,
        }
        if start_date is not None:
            params["observation_start"] = start_date
        if end_date is not None:
            params["observation_end"] = end_date

        obs_data = _get("series/observations", params)
        if obs_data is None:
            return None

        observations = []
        for obs in obs_data.get("observations", []):
            raw = obs.get("value")
            value = float(raw) if raw not in (None, ".") else None
            observations.append({"date": obs.get("date"), "value": value})

        # Series metadata ----------------------------------------------------
        info = _fetch_series_info(series_id)

        return {
            "series_id": series_id,
            "title": info.get("title", ""),
            "units": info.get("units", ""),
            "observations": observations,
        }
    except Exception:
        logger.exception("get_series failed for %s", series_id)
        return None


def get_yield_curve() -> dict | None:
    """Fetch the current US Treasury yield curve.

    Returns
    -------
    dict | None
        ``{date, rates: {1mo: x, ..., 30yr: x}, inverted: bool,
        spread_10y2y: float}`` or the no-key error dict, or None on failure.
    """
    if not _check_api_key():
        return dict(_NO_KEY_ERROR)

    try:
        rates: dict[str, float | None] = {}
        latest_date: str | None = None

        for series_id, label in _YIELD_CURVE_SERIES.items():
            result = _latest_value(series_id)
            if result is not None:
                rates[label] = result["value"]
                # Track the most recent date across all maturities.
                if latest_date is None or (
                    result["date"] is not None and result["date"] > latest_date
                ):
                    latest_date = result["date"]
            else:
                rates[label] = None

        rate_10y = rates.get("10yr")
        rate_2y = rates.get("2yr")

        if rate_10y is not None and rate_2y is not None:
            spread = round(rate_10y - rate_2y, 4)
            inverted = rate_10y < rate_2y
        else:
            spread = None
            inverted = None

        return {
            "date": latest_date,
            "rates": rates,
            "inverted": inverted,
            "spread_10y2y": spread,
        }
    except Exception:
        logger.exception("get_yield_curve failed")
        return None


def get_economic_snapshot() -> dict | None:
    """Fetch latest values for key macroeconomic indicators.

    Returns
    -------
    dict | None
        ``{date, indicators: {gdp_growth: {value, date, name}, ...}}``
        or the no-key error dict, or None on failure.
    """
    if not _check_api_key():
        return dict(_NO_KEY_ERROR)

    try:
        indicators: dict[str, dict] = {}
        latest_date: str | None = None

        for key, (series_id, display_name) in _SNAPSHOT_SERIES.items():
            result = _latest_value(series_id)
            if result is not None:
                indicators[key] = {
                    "value": result["value"],
                    "date": result["date"],
                    "name": display_name,
                    "series_id": series_id,
                }
                if latest_date is None or (
                    result["date"] is not None and result["date"] > latest_date
                ):
                    latest_date = result["date"]
            else:
                indicators[key] = {
                    "value": None,
                    "date": None,
                    "name": display_name,
                    "series_id": series_id,
                }

        return {
            "date": latest_date,
            "indicators": indicators,
        }
    except Exception:
        logger.exception("get_economic_snapshot failed")
        return None


def search_series(query: str, limit: int = 10) -> list[dict] | None:
    """Search FRED for series matching *query*.

    Parameters
    ----------
    query:
        Free-text search string (e.g. ``"consumer price index"``).
    limit:
        Maximum number of results.

    Returns
    -------
    list[dict] | None
        ``[{series_id, title, frequency, units, popularity}]``
        or the no-key error dict (as a single-element list wrapping the
        error dict for type consistency), or None on failure.
    """
    if not _check_api_key():
        return [dict(_NO_KEY_ERROR)]

    try:
        data = _get(
            "series/search",
            {"search_text": query, "limit": limit},
        )
        if data is None:
            return None

        results = []
        for series in data.get("seriess", []):
            results.append({
                "series_id": series.get("id", ""),
                "title": series.get("title", ""),
                "frequency": series.get("frequency", ""),
                "units": series.get("units", ""),
                "popularity": series.get("popularity", 0),
            })

        return results
    except Exception:
        logger.exception("search_series failed for query=%s", query)
        return None
