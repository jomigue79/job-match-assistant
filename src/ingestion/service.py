import asyncio
from typing import List, Optional, Sequence, Tuple, Union
from observability import get_logger
from .types import PartialFetchError, RawRecord, ScrapeQuery, ScraperError
from .scraper_client import ScraperClient
from .cache import RawScrapeCache

logger = get_logger("ingestion_service")

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

    async def fetch(self, queries: Union[ScrapeQuery, Sequence[ScrapeQuery]]) -> List[RawRecord]:
        """
        Fetches search results for one query or several and returns the pooled records.
        The client is called once per query, except a client that replays a whole cached
        run, which is called once. If write_to_cache is True, writes one envelope for the fetch.

        A query raising ScraperError does not stop the others. If every query fails, the
        error is raised: unchanged for a single query, wrapped for several. If only some
        fail, PartialFetchError is raised carrying the records the rest returned.
        """
        query_list = [queries] if isinstance(queries, ScrapeQuery) else list(queries)
        if not query_list:
            raise ValueError("fetch() requires at least one ScrapeQuery.")

        if getattr(self.scraper_client, "replays_whole_run", False) and len(query_list) > 1:
            logger.info(
                "Replay client returns the whole cached run; fetching once",
                n_queries=len(query_list)
            )
            query_list = query_list[:1]

        total = len(query_list)
        pooled: List[RawRecord] = []
        succeeded: List[ScrapeQuery] = []
        failures: List[Tuple[ScrapeQuery, str]] = []
        last_error: Optional[ScraperError] = None

        for index, query in enumerate(query_list, start=1):
            logger.info(
                "Fetching query",
                query_index=index,
                query_total=total,
                terms=query.terms,
                location=query.location
            )
            try:
                # Execute blocking crawler client fetch in a thread
                records = await asyncio.to_thread(self.scraper_client.fetch, query)
            except ScraperError as e:
                logger.error(
                    "Query failed, continuing with remaining queries",
                    query_index=index,
                    query_total=total,
                    terms=query.terms,
                    error=str(e)
                )
                failures.append((query, str(e)))
                last_error = e
                continue

            logger.info(
                "Query fetched",
                query_index=index,
                query_total=total,
                terms=query.terms,
                n_records=len(records)
            )
            pooled.extend(records)
            succeeded.append(query)

        if not succeeded:
            if total == 1:
                raise last_error
            raise ScraperError(f"All {total} queries failed; last error: {last_error}") from last_error

        if self.write_to_cache:
            # Execute blocking cache write in a thread
            await asyncio.to_thread(self.cache.write, pooled, self.source, succeeded)

        if failures:
            raise PartialFetchError(pooled, failures)

        return pooled

    async def ingest(self, query: ScrapeQuery, run_id: str) -> List[RawRecord]:
        """
        Legacy alias supporting earlier task test calls.
        """
        return await self.fetch(query)
