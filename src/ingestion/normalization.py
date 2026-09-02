from dataclasses import dataclass
from datetime import datetime, timezone
import html
import re
from typing import Any, Dict, List, Optional, Protocol
from config import get_settings
from domain import JobIdentity, JobPosting, JobStatus
from observability import get_logger
from .types import RawRecord

logger = get_logger("normalization")

class NormalizationError(Exception):
    """Raised when a raw record cannot be normalized due to missing/invalid required fields."""
    def __init__(self, message: str, record_id: str):
        super().__init__(f"{message} (Record Identifier: {record_id})")
        self.record_id = record_id

class SourceAdapter(Protocol):
    """
    Protocol defining the interface for source-specific field extractions.
    """
    source_name: str

    def extract(self, raw: RawRecord) -> Dict[str, Optional[str]]:
        """
        Extracts company, title, location, url, and description from raw records.
        Raises NormalizationError if required fields (company, title) are absent/empty.
        """
        ...

def _clean_text(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()

def _clean_html_description(val: Any) -> Optional[str]:
    if val is None:
        return None
    text = str(val)
    # Strip HTML tags
    no_tags = re.sub(r"<[^>]+>", " ", text)
    # Unescape html entities
    unescaped = html.unescape(no_tags)
    # Collapse whitespace
    collapsed = re.sub(r"\s+", " ", unescaped)
    return collapsed.strip()

class LinkedInJobsAdapter:
    """
    Adapter mapping Apify LinkedIn jobs scraper raw records.
    """
    source_name = "apify_linkedin"

    def extract(self, raw: RawRecord) -> Dict[str, Optional[str]]:
        company = _clean_text(raw.get("company_name") or raw.get("company"))
        title = _clean_text(raw.get("title"))
        url = _clean_text(raw.get("platform_url") or raw.get("url"))
        
        record_id = url or "unknown"
        
        if not company:
            raise NormalizationError("Required field 'company' is missing or empty.", record_id=record_id)
        if not title:
            raise NormalizationError("Required field 'title' is missing or empty.", record_id=record_id)
            
        location = raw.get("location")
        location_str = _clean_text(location) if location is not None else None
        
        description = raw.get("description")
        description_str = _clean_html_description(description) if description is not None else None
        
        platform = raw.get("platform")
        platform_str = _clean_text(platform) if platform is not None else self.source_name

        return {
            "company": company,
            "title": title,
            "location": location_str,
            "url": url,
            "description": description_str,
            "source": platform_str
        }

class IndeedJobsAdapter:
    """
    Adapter mapping Apify Indeed jobs scraper raw records.
    """
    source_name = "apify_indeed"

    def extract(self, raw: RawRecord) -> Dict[str, Optional[str]]:
        company = _clean_text(raw.get("company_name") or raw.get("company"))
        title = _clean_text(raw.get("title"))
        url = _clean_text(raw.get("platform_url") or raw.get("url"))
        
        record_id = url or "unknown"
        
        if not company:
            raise NormalizationError("Required field 'company' is missing or empty.", record_id=record_id)
        if not title:
            raise NormalizationError("Required field 'title' is missing or empty.", record_id=record_id)
            
        location = raw.get("location")
        location_str = _clean_text(location) if location is not None else None
        
        description = raw.get("description")
        description_str = _clean_html_description(description) if description is not None else None
        
        platform = raw.get("platform")
        platform_str = _clean_text(platform) if platform is not None else self.source_name

        return {
            "company": company,
            "title": title,
            "location": location_str,
            "url": url,
            "description": description_str,
            "source": platform_str
        }

# Adapter Registry Setup
ADAPTER_REGISTRY: Dict[str, SourceAdapter] = {}

def register_adapter(adapter_cls: Any) -> Any:
    instance = adapter_cls()
    ADAPTER_REGISTRY[instance.source_name] = instance
    return adapter_cls

# Register default adapters
register_adapter(LinkedInJobsAdapter)
register_adapter(IndeedJobsAdapter)

# Map agentx_all_jobs to the same adapter class as apify_linkedin
ADAPTER_REGISTRY["agentx_all_jobs"] = ADAPTER_REGISTRY["apify_linkedin"]

def get_adapter(source_name: str) -> SourceAdapter:
    if source_name not in ADAPTER_REGISTRY:
        raise ValueError(
            f"Unknown source adapter name: '{source_name}'. "
            f"Registered adapters: {list(ADAPTER_REGISTRY.keys())}"
        )
    return ADAPTER_REGISTRY[source_name]

@dataclass
class NormalizeResult:
    job_postings: List[JobPosting]
    errors: List[Dict[str, Any]]

class Normalizer:
    """
    Batch normalization utility mapping raw payloads to Pydantic JobPosting entities.
    Resolves the source-specific adapter from the registry.
    """
    def __init__(self, source_name: Optional[str] = None):
        settings = get_settings()
        self.source_name = source_name if source_name is not None else settings.scraper_source
        self.adapter = get_adapter(self.source_name)

    def normalize(
        self,
        records: List[RawRecord],
        scraped_at: Optional[datetime] = None
    ) -> NormalizeResult:
        """
        Runs batch normalization over a list of raw records.
        """
        postings = []
        errors = []
        
        scrape_time = scraped_at if scraped_at is not None else datetime.now(timezone.utc)
        
        for idx, record in enumerate(records):
            try:
                extracted = self.adapter.extract(record)
                
                # Construct domain identity matching canonical normalizations
                identity = JobIdentity(
                    company=extracted["company"],
                    title=extracted["title"],
                    location=extracted["location"]
                )
                
                # Construct domain JobPosting
                posting = JobPosting(
                    company=extracted["company"],
                    title=extracted["title"],
                    location=extracted["location"],
                    url=extracted["url"],
                    description=extracted["description"],
                    source=extracted.get("source") or self.adapter.source_name,
                    scraped_at=scrape_time,
                    status=JobStatus.SCRAPED,
                    identity_hash=identity.hash
                )
                postings.append(posting)
                
            except NormalizationError as e:
                # Log error and skip malformed record
                logger.warning(
                    "Skipping malformed raw record",
                    index=idx,
                    record_id=e.record_id,
                    reason=str(e)
                )
                errors.append({
                    "index": idx,
                    "record_id": e.record_id,
                    "error": str(e),
                    "raw": record
                })
            except Exception as e:
                # Catch any unexpected parsing failures
                record_id = record.get("url") or f"index_{idx}"
                logger.warning(
                    "Unexpected normalization failure",
                    index=idx,
                    record_id=record_id,
                    reason=str(e)
                )
                errors.append({
                    "index": idx,
                    "record_id": record_id,
                    "error": f"Unexpected error: {e}",
                    "raw": record
                })
                
        return NormalizeResult(job_postings=postings, errors=errors)
