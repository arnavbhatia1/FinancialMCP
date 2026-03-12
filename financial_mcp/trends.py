"""Google Trends data retrieval via pytrends.

Every public function catches exceptions internally and returns None or an
empty container — callers never need to handle pytrends errors.  Google
Trends is notoriously flaky (rate limits, geo-blocks, session expiry), so
a fresh TrendReq instance is created per call and a 1-second delay is
inserted before each API hit.
"""

import logging
import time

logger = logging.getLogger(__name__)

try:
    from pytrends.request import TrendReq
except ImportError:
    TrendReq = None  # type: ignore[assignment,misc]
    logger.warning(
        "pytrends is not installed — Google Trends functions will return None. "
        "Install it with: pip install pytrends"
    )

_VALID_TIMEFRAMES = frozenset({
    "now 7-d",
    "today 1-m",
    "today 3-m",
    "today 12-m",
    "today 5-y",
})

_VALID_RESOLUTIONS = frozenset({"COUNTRY", "REGION", "CITY", "DMA"})

_MAX_KEYWORDS = 5

_API_DELAY_SECONDS = 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_search_interest(
    keywords: list[str],
    timeframe: str = "today 3-m",
) -> dict | None:
    """Return search-interest time series for *keywords*, or None on failure.

    *keywords* may contain up to 5 terms (Google Trends limit).  Values in
    the returned data are 0-100 (relative interest within the timeframe).

    Returns::

        {
            "keywords": ["AAPL", "MSFT"],
            "timeframe": "today 3-m",
            "data": [
                {"date": "2026-01-04", "AAPL": 72, "MSFT": 55},
                ...
            ],
        }
    """
    if TrendReq is None:
        return None

    if not keywords or len(keywords) > _MAX_KEYWORDS:
        logger.error(
            "get_search_interest: keywords must be a non-empty list of "
            "at most %d items, got %d",
            _MAX_KEYWORDS,
            len(keywords) if keywords else 0,
        )
        return None

    if timeframe not in _VALID_TIMEFRAMES:
        logger.error(
            "get_search_interest: invalid timeframe %r (valid: %s)",
            timeframe,
            ", ".join(sorted(_VALID_TIMEFRAMES)),
        )
        return None

    try:
        pytrends = TrendReq()
        pytrends.build_payload(keywords, timeframe=timeframe)
        time.sleep(_API_DELAY_SECONDS)
        df = pytrends.interest_over_time()

        if df is None or df.empty:
            logger.info(
                "get_search_interest: empty result for %s", keywords
            )
            return None

        # Drop the boolean 'isPartial' column that pytrends appends.
        if "isPartial" in df.columns:
            df = df.drop(columns=["isPartial"])

        records: list[dict] = []
        for date, row in df.iterrows():
            entry: dict = {"date": date.strftime("%Y-%m-%d")}
            for kw in keywords:
                if kw in row:
                    entry[kw] = int(row[kw])
            records.append(entry)

        return {
            "keywords": keywords,
            "timeframe": timeframe,
            "data": records,
        }
    except Exception:
        logger.exception("get_search_interest failed for %s", keywords)
        return None


def get_trending_searches(country: str = "united_states") -> list[str] | None:
    """Return currently trending search terms for *country*, or None on failure.

    Returns a plain list of strings, e.g. ``["NVIDIA earnings", ...]``.
    """
    if TrendReq is None:
        return None

    try:
        pytrends = TrendReq()
        time.sleep(_API_DELAY_SECONDS)
        df = pytrends.trending_searches(pn=country)

        if df is None or df.empty:
            logger.info(
                "get_trending_searches: empty result for country=%s", country
            )
            return None

        return [str(term) for term in df[0].tolist()]
    except Exception:
        logger.exception(
            "get_trending_searches failed for country=%s", country
        )
        return None


def get_related_queries(keyword: str) -> dict | None:
    """Return top and rising related queries for *keyword*, or None on failure.

    Returns::

        {
            "keyword": "TSLA",
            "top":    [{"query": "tesla stock", "value": 100}, ...],
            "rising": [{"query": "tesla robotaxi", "value": 4550}, ...],
        }
    """
    if TrendReq is None:
        return None

    if not keyword:
        logger.error("get_related_queries: keyword must be a non-empty string")
        return None

    try:
        pytrends = TrendReq()
        pytrends.build_payload([keyword])
        time.sleep(_API_DELAY_SECONDS)
        related = pytrends.related_queries()

        if not related or keyword not in related:
            logger.info(
                "get_related_queries: no data for %r", keyword
            )
            return None

        bucket = related[keyword]

        top_records = _df_to_records(bucket.get("top"))
        rising_records = _df_to_records(bucket.get("rising"))

        return {
            "keyword": keyword,
            "top": top_records,
            "rising": rising_records,
        }
    except Exception:
        logger.exception("get_related_queries failed for %r", keyword)
        return None


def get_interest_by_region(
    keyword: str,
    resolution: str = "COUNTRY",
) -> dict | None:
    """Return regional interest for *keyword*, or None on failure.

    *resolution* must be one of ``COUNTRY``, ``REGION``, ``CITY``, or ``DMA``.

    Returns::

        {
            "keyword": "AAPL",
            "resolution": "COUNTRY",
            "regions": [
                {"name": "United States", "value": 100},
                {"name": "Canada", "value": 42},
                ...
            ],
        }
    """
    if TrendReq is None:
        return None

    if not keyword:
        logger.error(
            "get_interest_by_region: keyword must be a non-empty string"
        )
        return None

    if resolution not in _VALID_RESOLUTIONS:
        logger.error(
            "get_interest_by_region: invalid resolution %r (valid: %s)",
            resolution,
            ", ".join(sorted(_VALID_RESOLUTIONS)),
        )
        return None

    try:
        pytrends = TrendReq()
        pytrends.build_payload([keyword])
        time.sleep(_API_DELAY_SECONDS)
        df = pytrends.interest_by_region(resolution=resolution)

        if df is None or df.empty:
            logger.info(
                "get_interest_by_region: empty result for %r", keyword
            )
            return None

        # The DataFrame is indexed by region name with a single column for
        # the keyword.  Filter out zero-interest regions to keep the
        # payload compact.
        regions: list[dict] = []
        for region_name, row in df.iterrows():
            value = int(row[keyword]) if keyword in row else 0
            if value > 0:
                regions.append({"name": str(region_name), "value": value})

        # Sort descending by interest value for convenience.
        regions.sort(key=lambda r: r["value"], reverse=True)

        return {
            "keyword": keyword,
            "resolution": resolution,
            "regions": regions,
        }
    except Exception:
        logger.exception(
            "get_interest_by_region failed for %r (resolution=%s)",
            keyword,
            resolution,
        )
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _df_to_records(df) -> list[dict]:
    """Convert a pytrends related-queries DataFrame to a list of dicts.

    Returns an empty list if *df* is None or empty.  Each dict has keys
    ``query`` (str) and ``value`` (int).
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return []

    records: list[dict] = []
    for _, row in df.iterrows():
        entry: dict = {}
        if "query" in row:
            entry["query"] = str(row["query"])
        if "value" in row:
            entry["value"] = int(row["value"])
        if entry:
            records.append(entry)
    return records
