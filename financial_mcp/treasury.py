"""US Treasury data retrieval module.

Provides functions to fetch treasury interest rates, yield curve data,
national debt figures, and auction results via the Treasury.gov Fiscal Data
API (https://api.fiscaldata.treasury.gov) and the Treasury OData feed.
No authentication is required.

Every public function catches exceptions internally and returns None or an
empty container -- callers never need to handle Treasury API errors.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "FinancialMCP/1.0 (financial-mcp-server)",
    "Accept": "application/json",
})

_FISCAL_DATA_BASE = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
)

# The legacy data.treasury.gov OData feed was retired (it now returns an HTML
# page). The daily par yield curve is published as an OData/Atom XML feed here:
_TREASURY_YIELD_XML = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
)

# XML namespaces used by the Treasury Atom feed.
_ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
}

_ALLOWED_SECURITY_TYPES = {
    "Treasury Bills",
    "Treasury Notes",
    "Treasury Bonds",
    "Treasury Inflation-Protected Securities (TIPS)",
}

_YIELD_CURVE_FIELDS = {
    "BC_1MONTH":  "1mo",
    "BC_2MONTH":  "2mo",
    "BC_3MONTH":  "3mo",
    "BC_6MONTH":  "6mo",
    "BC_1YEAR":   "1yr",
    "BC_2YEAR":   "2yr",
    "BC_3YEAR":   "3yr",
    "BC_5YEAR":   "5yr",
    "BC_7YEAR":   "7yr",
    "BC_10YEAR":  "10yr",
    "BC_20YEAR":  "20yr",
    "BC_30YEAR":  "30yr",
}

_REQUEST_TIMEOUT = 15  # seconds


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fiscal_get(url: str) -> requests.Response | None:
    """Perform a GET against a Fiscal Data or Treasury endpoint.

    Returns the Response on success, or None on any failure.
    """
    try:
        resp = _SESSION.get(url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except Exception:
        logger.exception("Treasury request failed: %s", url)
        return None


def _safe_float(value) -> float | None:
    """Coerce *value* to float, returning None if conversion fails."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_treasury_rates(days: int = 30) -> dict | None:
    """Return recent average interest rates for major Treasury securities.

    Queries the Fiscal Data ``avg_interest_rates`` endpoint, sorted by most
    recent record date, and filters to Treasury Bills, Notes, Bonds, and TIPS.

    Returns ``{"rates": [{"date": ..., "security_type": ...,
    "avg_interest_rate": ...}, ...]}`` or None on failure.
    """
    try:
        # avg_interest_rates is monthly with ~15-20 rows per month (one per
        # security description). Fetch a wide enough window that the recent
        # marketable types are always captured, even for a small `days`.
        page_size = max(days, 40)
        url = (
            f"{_FISCAL_DATA_BASE}/v2/accounting/od/avg_interest_rates"
            f"?sort=-record_date&page[size]={page_size}"
        )
        resp = _fiscal_get(url)
        if resp is None:
            return None

        payload = resp.json()
        records = payload.get("data", [])
        if not records:
            return {"rates": []}

        rates = []
        for rec in records:
            # The human-readable security name ("Treasury Bills", "Treasury
            # Notes", ...) lives in `security_desc`. `security_type_desc` only
            # holds "Marketable"/"Non-marketable", so filtering on it returned
            # nothing.
            security_type = rec.get("security_desc", "")
            if security_type not in _ALLOWED_SECURITY_TYPES:
                continue

            rate = _safe_float(rec.get("avg_interest_rate_amt"))
            rates.append({
                "date": rec.get("record_date", ""),
                "security_type": security_type,
                "avg_interest_rate": rate,
            })

        return {"rates": rates}
    except Exception:
        logger.exception("get_treasury_rates failed")
        return None


def _fetch_yield_xml(year: int) -> list[dict]:
    """Fetch and parse the Treasury daily yield-curve Atom feed for *year*.

    Returns a list of ``{"date": ..., "1mo": ..., ...}`` rows (oldest first),
    or an empty list if the year has no data.
    """
    resp = _fiscal_get(_TREASURY_YIELD_XML.format(year=year))
    if resp is None:
        return []

    root = ET.fromstring(resp.content)
    rows: list[dict] = []
    for props in root.findall(".//m:properties", _ATOM_NS):
        date_el = props.find("d:NEW_DATE", _ATOM_NS)
        date_str = (date_el.text or "").split("T")[0] if date_el is not None else ""
        row: dict = {"date": date_str}
        for xml_key, label in _YIELD_CURVE_FIELDS.items():
            el = props.find(f"d:{xml_key}", _ATOM_NS)
            row[label] = _safe_float(el.text if el is not None else None)
        rows.append(row)
    return rows


def get_yield_curve_daily(days: int = 5) -> list[dict] | None:
    """Return the daily Treasury par yield curve for the most recent *days* days.

    Sourced from Treasury's daily yield-curve XML feed (the legacy OData feed
    was retired). Each row has ``"date"`` plus maturity keys (``"1mo"``,
    ``"2mo"``, ... ``"30yr"``), with yields in percent. Returns the rows
    newest-first, or None on failure.
    """
    try:
        year = datetime.now(timezone.utc).year
        rows = _fetch_yield_xml(year)
        # Early in a new year the current-year feed can be empty; fall back.
        if not rows:
            rows = _fetch_yield_xml(year - 1)

        # Feed is chronological; newest dates are last. Return newest-first.
        rows.sort(key=lambda r: r["date"], reverse=True)
        return rows[:days]
    except Exception:
        logger.exception("get_yield_curve_daily failed")
        return None


def get_debt_outstanding() -> dict | None:
    """Return the most recent total public debt outstanding.

    Queries the Fiscal Data ``debt_to_penny`` endpoint for the latest record.

    Returns ``{"date": ..., "total_debt": ..., "public_debt": ...,
    "intragovernmental_debt": ...}`` (values are floats in dollars) or None
    on failure.
    """
    try:
        url = (
            f"{_FISCAL_DATA_BASE}/v2/accounting/od/debt_to_penny"
            f"?sort=-record_date&page[size]=1"
        )
        resp = _fiscal_get(url)
        if resp is None:
            return None

        payload = resp.json()
        records = payload.get("data", [])
        if not records:
            return None

        rec = records[0]
        return {
            "date": rec.get("record_date", ""),
            "total_debt": _safe_float(rec.get("tot_pub_debt_out_amt")),
            "public_debt": _safe_float(rec.get("debt_held_public_amt")),
            "intragovernmental_debt": _safe_float(
                rec.get("intragov_hold_amt")
            ),
        }
    except Exception:
        logger.exception("get_debt_outstanding failed")
        return None


def get_treasury_auctions(
    security_type: str | None = None,
    days: int = 30,
) -> list[dict] | None:
    """Return recent Treasury auction results.

    Queries the Fiscal Data ``auctions_query`` endpoint.  Optionally filters
    to a single *security_type* (e.g. ``"Bill"``, ``"Note"``, ``"Bond"``).

    Returns a list of dicts with keys: auction_date, security_type,
    security_term, high_yield, bid_to_cover_ratio, total_accepted.
    Returns None on failure or an empty list if no results.
    """
    try:
        url = (
            f"{_FISCAL_DATA_BASE}/v1/accounting/od/auctions_query"
            f"?sort=-auction_date&page[size]={days}"
        )
        if security_type is not None:
            url += f"&filter=security_type:eq:{security_type}"

        resp = _fiscal_get(url)
        if resp is None:
            return None

        payload = resp.json()
        records = payload.get("data", [])
        if not records:
            return []

        results = []
        for rec in records:
            results.append({
                "auction_date": rec.get("auction_date", ""),
                "security_type": rec.get("security_type", ""),
                "security_term": rec.get("security_term", ""),
                "high_yield": _safe_float(rec.get("high_yield")),
                "bid_to_cover_ratio": _safe_float(
                    rec.get("bid_to_cover_ratio")
                ),
                "total_accepted": _safe_float(
                    rec.get("total_accepted")
                ),
            })

        return results
    except Exception:
        logger.exception("get_treasury_auctions failed")
        return None
