from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict

# RawRecord: opaque raw record representing search output from source.
RawRecord = Dict[str, Any]

class ScrapeQuery(BaseModel):
    """
    Source-agnostic representation of terms and constraints for scraping jobs.
    """
    model_config = ConfigDict(frozen=True)

    terms: str
    location: Optional[str] = None
    limit: int
    country: Optional[str] = None
    posted_since: Optional[str] = None

class ScraperError(Exception):
    """Raised on any scraping or crawler adapter failure."""
    pass

class CacheError(Exception):
    """Raised on any raw-scrape cache validation or reading failure."""
    pass

class PartialFetchError(Exception):
    """
    Raised when some queries of a multi-query fetch failed and the rest succeeded.
    Carries the records the successful queries returned so the run can continue with them.
    Deliberately not a ScraperError: a broad `except ScraperError` must not discard those records.
    """
    def __init__(self, records: List[RawRecord], failures: List[Tuple[ScrapeQuery, str]]):
        self.records = records
        self.failures = failures
        failed_terms = ", ".join(repr(query.terms) for query, _ in failures)
        super().__init__(
            f"{len(failures)} query(ies) failed ({failed_terms}); "
            f"{len(records)} record(s) fetched by the rest"
        )

class CacheEntry(BaseModel):
    """
    Metadata representation of a cached scrape file.
    """
    model_config = ConfigDict(frozen=True)

    path: Path
    source: str
    fetched_at: datetime
    count: int
