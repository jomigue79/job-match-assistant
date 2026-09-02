import asyncio
from typing import List
from .types import RawRecord, ScrapeQuery
from .scraper_client import ScraperClient
from .cache import RawScrapeCache

class IngestionService:
    """
    Service coordinating the crawler fetch and raw JSON cache writes.
    """
    def __init__(
        self,
        scraper_client: ScraperClient,
        cache: RawScrapeCache,
        source: str,
        write_to_cache: bool = True
    ):
        self.scraper_client = scraper_client
        self.cache = cache
        self.source = source
        self.write_to_cache = write_to_cache

    async def fetch(self, query: ScrapeQuery) -> List[RawRecord]:
        """
        Fetches search results from client. If write_to_cache is True, writes payload to cache.
        """
        # Execute blocking crawler client fetch in a thread
        records = await asyncio.to_thread(self.scraper_client.fetch, query)
        
        if self.write_to_cache:
            # Execute blocking cache write in a thread
            await asyncio.to_thread(self.cache.write, records, self.source, query)
            
        return records

    async def ingest(self, query: ScrapeQuery, run_id: str) -> List[RawRecord]:
        """
        Legacy alias supporting earlier task test calls.
        """
        return await self.fetch(query)
