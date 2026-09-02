import asyncio
import json
from unittest.mock import MagicMock, patch
import pytest
from ingestion import (
    ApifyScraperClient,
    ScrapeQuery,
    ScraperError,
    RawScrapeCache,
    IngestionService,
)

# Fake client definition for service composition testing
class FakeScraperClient:
    def __init__(self):
        self.calls = []

    def fetch(self, query: ScrapeQuery) -> list:
        self.calls.append(query)
        return [{"jobId": "job-abc", "title": query.terms}]

def test_apify_scraper_client_api_token_presence():
    # Constructor must raise ScraperError immediately if API token is missing/empty
    with pytest.raises(ScraperError) as exc_info:
        ApifyScraperClient(api_token=None, actor_id="my-actor")
    assert "token is required" in str(exc_info.value).lower()
    
    with pytest.raises(ScraperError):
        ApifyScraperClient(api_token="", actor_id="my-actor")

def test_apify_scraper_client_fetch_success():
    mock_client_instance = MagicMock()
    mock_actor = MagicMock()
    mock_dataset = MagicMock()
    
    mock_client_instance.actor.return_value = mock_actor
    # Mocking call return value as a Run object with attributes
    mock_run = MagicMock()
    mock_run.default_dataset_id = "dataset-999"
    mock_run.stats = MagicMock()
    mock_run.stats.compute_units = 0.35
    mock_actor.call.return_value = mock_run
    
    mock_client_instance.dataset.return_value = mock_dataset
    mock_dataset.list_items.return_value.items = [
        {"jobId": "123", "company": "Acme", "title": "Developer"},
        {"jobId": "456", "company": "Beta", "title": "Engineer"}
    ]
    
    # Mock class initialization
    with patch("ingestion.apify_client.ApifyClient", return_value=mock_client_instance):
        client = ApifyScraperClient(api_token="dummy-api-token", actor_id="test-actor")
        query = ScrapeQuery(terms="Python", location="Remote", limit=5)
        
        records = client.fetch(query)
        
        # Verify call structures
        mock_client_instance.actor.assert_called_once_with("test-actor")
        mock_actor.call.assert_called_once_with(
            run_input={
                "keyword": "Python",
                "location": "Remote",
                "max_results": 5,
                "country": None,
                "posted_since": None
            }
        )
        mock_client_instance.dataset.assert_called_once_with("dataset-999")
        
        assert len(records) == 2
        assert records[0]["company"] == "Acme"
        assert records[1]["title"] == "Engineer"

def test_apify_scraper_client_fetch_network_error():
    mock_client_instance = MagicMock()
    mock_actor = MagicMock()
    mock_client_instance.actor.return_value = mock_actor
    
    # Simulate network exception from call
    mock_actor.call.side_effect = Exception("SDK connection failed")
    
    with patch("ingestion.apify_client.ApifyClient", return_value=mock_client_instance):
        client = ApifyScraperClient(api_token="dummy-api-token", actor_id="test-actor")
        query = ScrapeQuery(terms="C#", location=None, limit=2)
        
        with pytest.raises(ScraperError) as exc_info:
            client.fetch(query)
            
        assert "Apify actor call failed" in str(exc_info.value)

def test_apify_scraper_client_missing_dataset_id():
    mock_client_instance = MagicMock()
    mock_actor = MagicMock()
    mock_client_instance.actor.return_value = mock_actor
    # Missing defaultDatasetId as an attribute of a Run object
    mock_run = MagicMock()
    mock_run.default_dataset_id = None
    mock_actor.call.return_value = mock_run
    
    with patch("ingestion.apify_client.ApifyClient", return_value=mock_client_instance):
        client = ApifyScraperClient(api_token="dummy-api-token", actor_id="test-actor")
        query = ScrapeQuery(terms="Go", location="New York", limit=1)
        
        with pytest.raises(ScraperError) as exc_info:
            client.fetch(query)
            
        assert "default_dataset_id" in str(exc_info.value)

def test_raw_scrape_cache_saves_locally(tmp_path):
    cache = RawScrapeCache(str(tmp_path))
    records = [
        {"company": "Google", "role": "Senior SWE"},
        {"company": "Apple", "role": "UI Designer"}
    ]
    query = ScrapeQuery(terms="Engineering", location=None, limit=2)
    
    # Save cache
    saved_path = cache.write(records, "test_source", query)
    
    assert saved_path.exists()
    assert "scrape_test_source_" in saved_path.name
    
    # Read back and assert content equivalence
    with open(saved_path, "r", encoding="utf-8") as f:
        envelope = json.load(f)
        
    assert envelope["source"] == "test_source"
    assert envelope["query"]["terms"] == "Engineering"
    assert len(envelope["records"]) == 2
    assert envelope["records"][0]["company"] == "Google"
    assert envelope["records"][1]["role"] == "UI Designer"

@pytest.mark.asyncio
async def test_ingestion_service_orchestration(tmp_path):
    cache = RawScrapeCache(str(tmp_path))
    fake_client = FakeScraperClient()
    service = IngestionService(fake_client, cache, source="test_source")
    
    query = ScrapeQuery(terms="Rust Developer", location="Berlin", limit=15)
    
    # Execute async ingestion
    records = await service.fetch(query)
    
    assert len(records) == 1
    assert records[0]["title"] == "Rust Developer"
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0] == query
    
    # Verify cached output file
    cached_entries = cache.list_cached(source="test_source")
    assert len(cached_entries) == 1
    assert cached_entries[0].source == "test_source"
    assert cached_entries[0].count == 1
    
    data = cache.read(cached_entries[0].path)
    assert len(data) == 1
    assert data[0]["jobId"] == "job-abc"
