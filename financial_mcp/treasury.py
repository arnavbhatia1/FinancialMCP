"""US Treasury data retrieval module.

Provides functions to fetch treasury interest rates, yield curve data,
national debt figures, and auction results via the Treasury.gov Fiscal Data
API (https://api.fiscaldata.treasury.gov) and the Treasury OData feed.
No authentication is required.

Every public function catches exceptions internally and returns None or an
empty container -- callers never need to handle Treasury API errors.
"""

import logging
from datetime import datetime, timedelta

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

_TREASURY_ODATA_BASE = (
    "https://data.treasury.gov/feed.svc/DailyTreasuryYieldCurveRateData"
)

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
        url = (
            f"{_FISCAL_DATA_BASE}/v2/accounting/od/avg_interest_rates"
            f"?sort=-record_date&page[size]={days}"
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
            security_type = rec.get("security_type_desc", "")
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


def get_yield_curve_daily(days: int = 5) -> list[dict] | None:
    """Return daily Treasury yield curve data for the most recent *days* days.

    Uses the Treasury OData feed for ``DailyTreasuryYieldCurveRateData``.

    Returns a list of dicts, each containing ``"date"`` and maturity keys
    (``"1mo"``, ``"2mo"``, ... ``"30yr"``), with yields in percent.
    Returns None on failure or an empty list if no data is found.
    """
    try:
        now = datetime.utcnow()
        month = now.month
        year = now.year

        url = (
            f"{_TREASURY_ODATA_BASE}"
            f"?$filter=month(NEW_DATE) eq {month} and year(NEW_DATE) eq {year}"
            f"&$orderby=NEW_DATE desc"
            f"&$top={days}"
            f"&$format=json"
        )
        resp = _fiscal_get(url)
        if resp is None:
            # Fall back to previous month if current month has no data yet
            # (common at the start of a new month).
            prev = now.replace(day=1) - timedelta(days=1)
            url = (
                f"{_TREASURY_ODATA_BASE}"
                f"?$filter=month(NEW_DATE) eq {prev.month}"
                f" and year(NEW_DATE) eq {prev.year}"
                f"&$orderby=NEW_DATE desc"
                f"&$top={days}"
                f"&$format=json"
            )
            resp = _fiscal_get(url)
            if resp is None:
                return None

        payload = resp.json()
        # The OData feed nests results under "d" -> "value" or just "value".
        entries = payload.get("d", payload).get("value", [])
        if not entries:
            entries = payload.get("value", [])

        if not entries:
            return []

        results = []
        for entry in entries:
            date_raw = entry.get("NEW_DATE", "")
            # OData dates may look like "/Date(1234567890000)/" or ISO strings.
            date_str = _parse_odata_date(date_raw)

            row: dict = {"date": date_str}
            for odata_key, label in _YIELD_CURVE_FIELDS.items():
                row[label] = _safe_float(entry.get(odata_key))

            results.append(row)

        return results
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


# ---------------------------------------------------------------------------
# Internal helpers (continued)
# ---------------------------------------------------------------------------

def _parse_odata_date(value: str) -> str:
    """Convert an OData date representation to ``YYYY-MM-DD``.

    Handles three formats:
    - ``/Date(1234567890000)/`` (milliseconds since epoch)
    - ISO 8601 strings (``2026-03-01T00:00:00``)
    - Plain ``YYYY-MM-DD``

    Returns the original string unchanged if none of the formats match.
    """
    if not value:
        return ""

    # /Date(...)/ format
    if value.startswith("/Date(") and value.endswith(")/"):
        try:
            ms = int(value[6:-2])
            return datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return value

    # ISO 8601 with time component
    if "T" in value:
        try:
            return datetime.fromisoformat(value).strftime("%Y-%m-%d")
        except ValueError:
            return value

    # Already YYYY-MM-DD or unknown -- return as-is
    return value
