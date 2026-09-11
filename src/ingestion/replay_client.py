from typing import List, Optional
from .types import RawRecord, ScrapeQuery, ScraperError
from .cache import RawScrapeCache

class ReplayScraperClient:
    """
    Drop-in ScraperClient replaying raw scraper payloads from RawScrapeCache.
    Acts as an Apify-independent simulation engine.
    """
    # Replay ignores the query and returns the whole cached run, so a multi-query
    # fetch must call this client once rather than once per query.
    replays_whole_run = True

    def __init__(self, cache: RawScrapeCache, source: str, replay_path: Optional[str] = None):
        self.cache = cache
        self.source = source
        self.replay_path = replay_path

    def fetch(self, query: ScrapeQuery) -> List[RawRecord]:
        """
        Loads cached records from file, bypassing the network.
        Raises ScraperError if files are missing or unreadable.
        """
        if self.replay_path:
            try:
                return self.cache.read(self.replay_path)
            except Exception as e:
                raise ScraperError(
                    f"Failed to replay from specified path {self.replay_path}: {e}"
                ) from e
                
        # Read latest for source
        records = self.cache.read_latest(self.source)
        if records is None:
            raise ScraperError(
                f"No cached scrapes available to replay for source '{self.source}'."
            )
            
        return records
