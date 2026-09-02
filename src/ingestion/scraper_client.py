from typing import List, Protocol
from .types import RawRecord, ScrapeQuery

class ScraperClient(Protocol):
    """
    Protocol contract representing source-agnostic web crawler clients.
    """
    def fetch(self, query: ScrapeQuery) -> List[RawRecord]:
        """
        Fetches job records matching the query from the client source.
        Raises ScraperError on failures.
        """
        ...
