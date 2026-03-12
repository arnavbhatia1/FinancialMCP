"""CFTC Commitments of Traders (COT) data via the Socrata Open Data API.

Weekly positioning reports for futures markets.  Data is published every
Friday for the prior Tuesday.  The legacy futures-only endpoint is used
because it is the most widely documented and requires no authentication.

Every public function catches exceptions internally and returns None or an
empty container -- callers never need to handle API errors.
"""

import logging

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"
_REQUEST_TIMEOUT = 30

# Field mapping from the Socrata JSON response to our output keys.
_POSITION_FIELD_MAP = {
    "noncomm_positions_long_all": "non_commercial_long",
    "noncomm_positions_short_all": "non_commercial_short",
    "comm_positions_long_all": "commercial_long",
    "comm_positions_short_all": "commercial_short",
    "nonrept_positions_long_all": "non_reportable_long",
    "nonrept_positions_short_all": "non_reportable_short",
}


def get_positioning(market_name: str, limit: int = 10) -> dict | None:
    """Return recent COT positioning data for *market_name*.

    Queries the CFTC legacy futures-only report for rows whose
    ``market_and_exchange_names`` contains *market_name* (case-insensitive
    on the server side because SoQL ``like`` is case-insensitive).

    Returns::

        {
            "market": str,
            "reports": [
                {
                    "date": str,
                    "open_interest": int,
                    "commercial_long": int,
                    "commercial_short": int,
                    "commercial_net": int,
                    "non_commercial_long": int,
                    "non_commercial_short": int,
                    "non_commercial_net": int,
                    "non_reportable_long": int,
                    "non_reportable_short": int,
                },
                ...
            ],
        }

    Returns None on any failure.
    """
    try:
        params = {
            "$where": (
                f"market_and_exchange_names like '%{market_name}%'"
            ),
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": limit,
        }
        resp = requests.get(_BASE_URL, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()

        if not rows:
            logger.info("No COT data found for market '%s'", market_name)
            return None

        reports: list[dict] = []
        for row in rows:
            report = _parse_report_row(row)
            if report is not None:
                reports.append(report)

        if not reports:
            return None

        # Use the full market name from the first row for the response.
        market = rows[0].get("market_and_exchange_names", market_name)

        return {"market": market, "reports": reports}
    except Exception:
        logger.exception("get_positioning failed for '%s'", market_name)
        return None


def get_smart_money_signal(market_name: str) -> dict | None:
    """Assess whether commercial hedgers are signaling a directional bias.

    Commercials are considered "smart money" -- they are producers and
    consumers who hedge their real business exposure.  Extreme positioning
    relative to the recent range is a well-known contrarian signal.

    Fetches the last 20 weeks of COT data and computes where the current
    commercial net position sits within the range of available observations.

    Returns::

        {
            "market": str,
            "signal": "bullish" | "bearish" | "neutral",
            "commercial_net": int,
            "percentile": float,     # 0-100
            "weeks_analyzed": int,
        }

    Returns None on any failure or insufficient data.
    """
    try:
        result = get_positioning(market_name, limit=20)
        if result is None or not result["reports"]:
            return None

        reports = result["reports"]
        nets = [r["commercial_net"] for r in reports]
        weeks_analyzed = len(nets)

        if weeks_analyzed < 2:
            logger.info(
                "Insufficient data for smart money signal on '%s' "
                "(%d week(s))",
                market_name,
                weeks_analyzed,
            )
            return None

        current_net = nets[0]  # Most recent (sorted DESC)
        range_min = min(nets)
        range_max = max(nets)
        span = range_max - range_min

        if span == 0:
            percentile = 50.0
        else:
            percentile = round((current_net - range_min) / span * 100, 2)

        if percentile >= 90:
            signal = "bullish"
        elif percentile <= 10:
            signal = "bearish"
        else:
            signal = "neutral"

        return {
            "market": result["market"],
            "signal": signal,
            "commercial_net": current_net,
            "percentile": percentile,
            "weeks_analyzed": weeks_analyzed,
        }
    except Exception:
        logger.exception(
            "get_smart_money_signal failed for '%s'", market_name
        )
        return None


def list_markets(query: str | None = None) -> list[str]:
    """Return distinct market names available in the legacy COT report.

    Optionally filters to names containing *query*.  Returns an empty list
    on failure.
    """
    try:
        params: dict[str, str] = {
            "$select": "distinct market_and_exchange_names",
            "$order": "market_and_exchange_names ASC",
            "$limit": "5000",
        }
        if query:
            params["$where"] = (
                f"market_and_exchange_names like '%{query}%'"
            )

        resp = requests.get(_BASE_URL, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()

        return [
            row["market_and_exchange_names"]
            for row in rows
            if "market_and_exchange_names" in row
        ]
    except Exception:
        logger.exception("list_markets failed (query=%s)", query)
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_int(value) -> int:
    """Coerce *value* to int, returning 0 for None or unparseable input."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_report_row(row: dict) -> dict | None:
    """Transform a single Socrata JSON row into our report format.

    Returns None if the row is missing required date or open-interest fields.
    """
    date = row.get("report_date_as_yyyy_mm_dd")
    if date is None:
        return None

    # Socrata sometimes returns date strings with a time component.
    date = str(date)[:10]

    open_interest = _safe_int(row.get("open_interest_all"))
    if open_interest == 0:
        return None

    report: dict = {"date": date, "open_interest": open_interest}

    for src_field, dst_field in _POSITION_FIELD_MAP.items():
        report[dst_field] = _safe_int(row.get(src_field))

    report["commercial_net"] = (
        report["commercial_long"] - report["commercial_short"]
    )
    report["non_commercial_net"] = (
        report["non_commercial_long"] - report["non_commercial_short"]
    )

    return report
