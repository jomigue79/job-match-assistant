from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
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

class CacheEntry(BaseModel):
    """
    Metadata representation of a cached scrape file.
    """
    model_config = ConfigDict(frozen=True)

    path: Path
    source: str
    fetched_at: datetime
    count: int
