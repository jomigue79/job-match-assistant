from typing import Any, Dict, List, Union
from pydantic import SecretStr
from apify_client import ApifyClient
from observability import get_logger
from .types import RawRecord, ScrapeQuery, ScraperError

logger = get_logger("apify_scraper_client")

class ApifyScraperClient:
    """
    Apify-aware implementation of ScraperClient.
    Integrates with apify-client SDK.
    """
    def __init__(self, api_token: Union[str, SecretStr, None], actor_id: str):
        token_val = None
        if api_token:
            if isinstance(api_token, SecretStr):
                token_val = api_token.get_secret_value()
            else:
                token_val = str(api_token)

        if not token_val:
            raise ScraperError("Apify API token is required but missing.")

        self.api_token = token_val
        self.actor_id = actor_id
        self.client = ApifyClient(self.api_token)

    def _build_run_input(self, query: ScrapeQuery) -> Dict[str, Any]:
        """
        Builds the actor-specific run input dictionary from ScrapeQuery.
        This is the per-actor integration touch point to adjust when swapping actors.
        """
        return {
            "keyword": query.terms,
            "country": query.country,
            "location": query.location,
            "max_results": query.limit,
            "posted_since": query.posted_since,
        }

    def fetch(self, query: ScrapeQuery) -> List[RawRecord]:
        """
        Runs the configured Apify actor and extracts dataset items.
        """
        run_input = self._build_run_input(query)

        try:
            actor_call = self.client.actor(self.actor_id).call(
                run_input=run_input
            )
        except Exception as e:
            raise ScraperError(f"Apify actor call failed: {e}") from e

        if not actor_call:
            raise ScraperError("No response returned from Apify actor execution.")

        # SDK now returns a Pydantic Run object — use attribute access, not .get()
        dataset_id = actor_call.default_dataset_id
        if not dataset_id:
            raise ScraperError("No default_dataset_id returned from Apify actor run.")

        try:
            dataset_items = self.client.dataset(dataset_id).list_items().items

            # Stats are a nested object in the new SDK — guard for missing attributes
            try:
                compute_units = actor_call.stats.compute_units if actor_call.stats else 0.0
            except AttributeError:
                compute_units = 0.0

            logger.info(
                "Apify actor run completed",
                actor_id=self.actor_id,
                item_count=len(dataset_items),
                compute_units=compute_units,
            )

            return [dict(item) for item in dataset_items]

        except ScraperError:
            raise
        except Exception as e:
            raise ScraperError(f"Failed to fetch dataset items: {e}") from e