"""SEC EDGAR data retrieval module.

Provides functions to look up company filings, insider trades, and full-text
search via the SEC EDGAR API (https://data.sec.gov).  No authentication is
required; however, SEC mandates a descriptive User-Agent header.

Every public function catches exceptions internally and returns None or an
empty container -- callers never need to handle EDGAR errors.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "FinancialMCP/1.0 (arnav.cal@gmail.com)",
    "Accept": "application/json",
})

_cik_cache: dict[str, str] = {}  # symbol -> zero-padded CIK
_tickers_loaded: bool = False

_RATE_LIMIT_DELAY = 0.1  # seconds between SEC requests


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sec_get(url: str, *, timeout: int = 15) -> requests.Response | None:
    """Perform a GET request to an SEC endpoint with rate-limit delay.

    Returns the Response on success, or None on any failure.
    """
    try:
        time.sleep(_RATE_LIMIT_DELAY)
        resp = _SESSION.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception:
        logger.exception("SEC request failed: %s", url)
        return None


def _pad_cik(cik: int | str) -> str:
    """Zero-pad a CIK to 10 digits."""
    return str(int(cik)).zfill(10)


def _load_ticker_map() -> None:
    """Download the SEC company_tickers.json and populate ``_cik_cache``."""
    global _tickers_loaded  # noqa: PLW0603

    if _tickers_loaded:
        return

    url = "https://www.sec.gov/files/company_tickers.json"
    resp = _sec_get(url)
    if resp is None:
        logger.error("Failed to download company_tickers.json")
        return

    try:
        data = resp.json()
    except ValueError:
        logger.exception("Invalid JSON from company_tickers.json")
        return

    for entry in data.values():
        symbol = entry.get("ticker", "").upper()
        cik_raw = entry.get("cik_str")
        if symbol and cik_raw is not None:
            _cik_cache[symbol] = _pad_cik(cik_raw)

    _tickers_loaded = True
    logger.info("Loaded %d ticker-to-CIK mappings from SEC", len(_cik_cache))


def _filing_url(cik: str, accession: str, primary_doc: str) -> str:
    """Build the full URL to a filing's primary document."""
    acc_no_dashes = accession.replace("-", "")
    cik_unpadded = str(int(cik))
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_unpadded}/{acc_no_dashes}/{primary_doc}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_company_cik(symbol: str) -> str | None:
    """Return the zero-padded (10-digit) CIK for *symbol*, or None.

    On the first call this downloads the SEC ticker-to-CIK mapping file and
    caches it in memory for subsequent lookups.
    """
    try:
        _load_ticker_map()
        return _cik_cache.get(symbol.upper())
    except Exception:
        logger.exception("get_company_cik failed for %s", symbol)
        return None


def get_filings(
    symbol: str,
    filing_type: str = "10-K",
    count: int = 5,
) -> list[dict] | None:
    """Return the most recent *count* filings of *filing_type* for *symbol*.

    Each filing dict contains:
    filing_type, date, accession_number, primary_document_url, description.

    Returns None on failure, or an empty list if no matching filings exist.
    """
    try:
        cik = get_company_cik(symbol)
        if cik is None:
            logger.warning("No CIK found for %s", symbol)
            return None

        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = _sec_get(url)
        if resp is None:
            return None

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        if not recent:
            return []

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])

        results: list[dict] = []
        for i, form in enumerate(forms):
            if form != filing_type:
                continue

            accession = accessions[i] if i < len(accessions) else ""
            primary_doc = primary_docs[i] if i < len(primary_docs) else ""
            doc_url = _filing_url(cik, accession, primary_doc) if accession and primary_doc else ""

            results.append({
                "filing_type": form,
                "date": dates[i] if i < len(dates) else "",
                "accession_number": accession,
                "primary_document_url": doc_url,
                "description": descriptions[i] if i < len(descriptions) else "",
            })

            if len(results) >= count:
                break

        return results
    except Exception:
        logger.exception("get_filings failed for %s", symbol)
        return None


def get_insider_trades(symbol: str, days: int = 90) -> list[dict] | None:
    """Return recent Form 4 filings (insider transactions) for *symbol*.

    Looks back *days* calendar days from today.  Each entry contains:
    filing_date, form_type, accession_number, url.

    Returns None on failure, or an empty list if no Form 4s are found.
    """
    try:
        cik = get_company_cik(symbol)
        if cik is None:
            logger.warning("No CIK found for %s", symbol)
            return None

        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = _sec_get(url)
        if resp is None:
            return None

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        if not recent:
            return []

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        insider_forms = {"3", "4", "5"}

        results: list[dict] = []
        for i, form in enumerate(forms):
            if form not in insider_forms:
                continue

            filing_date = dates[i] if i < len(dates) else ""
            if filing_date < cutoff:
                continue

            accession = accessions[i] if i < len(accessions) else ""
            primary_doc = primary_docs[i] if i < len(primary_docs) else ""
            doc_url = _filing_url(cik, accession, primary_doc) if accession and primary_doc else ""

            results.append({
                "filing_date": filing_date,
                "form_type": form,
                "accession_number": accession,
                "url": doc_url,
            })

        return results
    except Exception:
        logger.exception("get_insider_trades failed for %s", symbol)
        return None


def get_filing_text(accession_number: str, cik: str) -> str | None:
    """Fetch and return the text of a filing's primary document (first 5000 chars).

    *cik* should be the zero-padded CIK (as returned by ``get_company_cik``).
    *accession_number* is the SEC accession number (e.g. ``"0001234567-24-000001"``).

    Returns None on failure.
    """
    try:
        padded_cik = _pad_cik(cik)

        # First, look up the primary document name from the filing index.
        url = f"https://data.sec.gov/submissions/CIK{padded_cik}.json"
        resp = _sec_get(url)
        if resp is None:
            return None

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        primary_doc = None
        for i, acc in enumerate(accessions):
            if acc == accession_number:
                primary_doc = primary_docs[i] if i < len(primary_docs) else None
                break

        if not primary_doc:
            logger.warning(
                "Primary document not found for accession %s", accession_number
            )
            return None

        doc_url = _filing_url(padded_cik, accession_number, primary_doc)
        doc_resp = _sec_get(doc_url)
        if doc_resp is None:
            return None

        text = doc_resp.text
        if len(text) > 5000:
            text = text[:5000]

        return text
    except Exception:
        logger.exception(
            "get_filing_text failed for accession=%s cik=%s",
            accession_number,
            cik,
        )
        return None


def search_filings(
    query: str,
    filing_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    count: int = 10,
) -> list[dict] | None:
    """Full-text search across EDGAR filings.

    *query* is the search term.  Optional filters: *filing_type* (e.g.
    ``"10-K"``), *date_from* / *date_to* (``"YYYY-MM-DD"`` strings).

    Each result dict contains: company_name, ticker, cik, filing_type, date,
    accession_number, url.

    Returns None on failure, or an empty list if no results.
    """
    try:
        params: dict[str, str] = {"q": query}

        if filing_type:
            params["forms"] = filing_type

        if date_from or date_to:
            params["dateRange"] = "custom"
            if date_from:
                params["startdt"] = date_from
            if date_to:
                params["enddt"] = date_to

        url = "https://efts.sec.gov/LATEST/search-index"
        resp = _sec_get(f"{url}?{urlencode(params)}")
        if resp is None:
            return None

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        results: list[dict] = []
        for hit in hits[:count]:
            source = hit.get("_source", {})
            file_date = source.get("file_date", "")
            entity_name = source.get("entity_name", "")
            form_type = source.get("form_type", "")
            file_num = source.get("file_num", "")
            tickers = source.get("tickers", "")
            display_names = source.get("display_names", [])

            # Extract the accession number from the _id field.
            accession = hit.get("_id", "")

            # Build a URL to the filing on SEC.
            acc_no_dashes = accession.replace("-", "")
            sec_url = (
                f"https://www.sec.gov/Archives/edgar/data/{acc_no_dashes}/"
                if accession
                else ""
            )

            results.append({
                "company_name": entity_name or (display_names[0] if display_names else ""),
                "ticker": tickers,
                "cik": file_num,
                "filing_type": form_type,
                "date": file_date,
                "accession_number": accession,
                "url": sec_url,
            })

        return results
    except Exception:
        logger.exception("search_filings failed for query=%r", query)
        return None


