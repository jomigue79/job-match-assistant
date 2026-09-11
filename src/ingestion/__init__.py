from typing import Optional
from .types import RawRecord, ScrapeQuery, ScraperError, CacheEntry, CacheError, PartialFetchError
from .scraper_client import ScraperClient
from .apify_client import ApifyScraperClient
from .cache import RawScrapeCache
from .replay_client import ReplayScraperClient
from .service import IngestionService
from .normalization import (
    NormalizationError,
    SourceAdapter,
    LinkedInJobsAdapter,
    IndeedJobsAdapter,
    Normalizer,
    NormalizeResult,
    get_adapter,
)
from .dedup import DedupResult, DedupService


def build_ingestion_service() -> IngestionService:
    """
    Factory creating an IngestionService instance.
    Consults configuration to wire either a live ApifyScraperClient
    or a ReplayScraperClient.
    """
    from config import get_settings
    settings = get_settings()
    
    cache = RawScrapeCache(settings.raw_scrape_dir)
    source = settings.scraper_source
    
    if settings.replay_from_cache:
        client = ReplayScraperClient(
            cache=cache,
            source=source,
            replay_path=settings.replay_cache_path
        )
        write_to_cache = False
    else:
        client = ApifyScraperClient(
            api_token=settings.apify_api_token,
            actor_id=settings.apify_actor_id
        )
        write_to_cache = True
        
    return IngestionService(
        scraper_client=client,
        cache=cache,
        source=source,
        write_to_cache=write_to_cache
    )

__all__ = [
    "RawRecord",
    "ScrapeQuery",
    "ScraperError",
    "PartialFetchError",
    "CacheEntry",
    "CacheError",
    "ScraperClient",
    "ApifyScraperClient",
    "RawScrapeCache",
    "ReplayScraperClient",
    "IngestionService",
    "build_ingestion_service",
    "NormalizationError",
    "SourceAdapter",
    "LinkedInJobsAdapter",
    "IndeedJobsAdapter",
    "Normalizer",
    "NormalizeResult",
    "get_adapter",
    "DedupResult",
    "DedupService",
]
