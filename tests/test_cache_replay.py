import asyncio
import json
import time
from unittest.mock import MagicMock, patch
import pytest
from config import get_settings
from ingestion import (
    RawScrapeCache,
    ScrapeQuery,
    CacheEntry,
    CacheError,
    ScraperError,
    ReplayScraperClient,
    ApifyScraperClient,
    IngestionService,
    build_ingestion_service,
)
from helpers import load_fixture_scrape

def test_cache_round_trip_and_list_cached(tmp_path):
    cache = RawScrapeCache(str(tmp_path))
    
    query = ScrapeQuery(terms="Python Developer", location="Remote", limit=5)
    records_1 = [{"jobId": "j1", "company": "Acme"}]
    records_2 = [{"jobId": "j2", "company": "Beta"}]
    
    # Write two scrapes sequentially
    path_1 = cache.write(records_1, "source_a", query)
    time.sleep(0.01) # ensure distinct timestamps for sorting
    path_2 = cache.write(records_2, "source_a", query)
    
    # List cached
    entries = cache.list_cached(source="source_a")
    assert len(entries) == 2
    
    # Assert newest-first ordering
    assert entries[0].path == path_2
    assert entries[1].path == path_1
    assert entries[0].count == 1
    assert entries[0].source == "source_a"
    
    # Read latest
    latest_records = cache.read_latest(source="source_a")
    assert len(latest_records) == 1
    assert latest_records[0]["jobId"] == "j2"
    
    # Read specific
    specific_records = cache.read(path_1)
    assert len(specific_records) == 1
    assert specific_records[0]["jobId"] == "j1"

def test_cache_read_invalid_envelope(tmp_path):
    cache = RawScrapeCache(str(tmp_path))
    
    corrupt_file = tmp_path / "scrape_corrupt.json"
    corrupt_file.write_text("this is not json at all", encoding="utf-8")
    
    with pytest.raises(CacheError) as exc_info:
        cache.read(corrupt_file)
    assert "parse cache file" in str(exc_info.value).lower()
    
    invalid_envelope_file = tmp_path / "scrape_invalid.json"
    # missing fetched_at
    invalid_envelope_file.write_text(
        json.dumps({"source": "apify", "records": []}),
        encoding="utf-8"
    )
    
    with pytest.raises(CacheError) as exc_info:
        cache.read(invalid_envelope_file)
    assert "envelope structure" in str(exc_info.value).lower()

def test_replay_scraper_client_fetch_fixtures():
    # Make sure apify client SDK is mocked to RAISE if instantiated
    # This guarantees that ReplayScraperClient performs zero network/SDK operations
    with patch("ingestion.apify_client.ApifyClient", side_effect=AssertionError("Live Apify SDK should not be initialized")):
        cache = MagicMock()
        # Mock read_latest to return fixture dataset
        cache.read_latest.return_value = [{"title": " SWE ", "company": "FixtureCorp"}]
        
        client = ReplayScraperClient(cache=cache, source="apify_linkedin", replay_path=None)
        query = ScrapeQuery(terms="SWE", location=None, limit=10)
        
        records = client.fetch(query)
        assert len(records) == 1
        assert records[0]["company"] == "FixtureCorp"
        cache.read_latest.assert_called_once_with("apify_linkedin")

def test_replay_scraper_client_empty_cache_raises():
    cache = MagicMock()
    cache.read_latest.return_value = None
    
    client = ReplayScraperClient(cache=cache, source="apify_linkedin", replay_path=None)
    query = ScrapeQuery(terms="SWE", location=None, limit=10)
    
    with pytest.raises(ScraperError) as exc_info:
        client.fetch(query)
    assert "no cached scrapes available" in str(exc_info.value).lower()

@pytest.mark.asyncio
async def test_ingestion_service_skips_cache_on_replay(tmp_path):
    cache = RawScrapeCache(str(tmp_path))
    
    # Pre-populate cache so client can read it
    query = ScrapeQuery(terms="SWE", location=None, limit=10)
    records = [{"title": "Pre-Cached Engineer"}]
    cache.write(records, "my_source", query)
    
    replay_client = ReplayScraperClient(cache=cache, source="my_source")
    
    # Wire IngestionService in replay mode (write_to_cache=False)
    service = IngestionService(
        scraper_client=replay_client,
        cache=cache,
        source="my_source",
        write_to_cache=False
    )
    
    # Capture listing count before fetch
    initial_entries_count = len(cache.list_cached(source="my_source"))
    assert initial_entries_count == 1
    
    # Run service fetch
    fetched_records = await service.fetch(query)
    assert len(fetched_records) == 1
    assert fetched_records[0]["title"] == "Pre-Cached Engineer"
    
    # Verify no new files were written in the raw_scrape_dir
    final_entries_count = len(cache.list_cached(source="my_source"))
    assert final_entries_count == 1

def test_factory_wiring(monkeypatch):
    # Test factory builds ReplayScraperClient when replay_from_cache is True
    monkeypatch.setenv("REPLAY_FROM_CACHE", "True")
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "dummy-llm-key")
    
    get_settings.cache_clear()
    
    service = build_ingestion_service()
    assert isinstance(service.scraper_client, ReplayScraperClient)
    assert service.write_to_cache is False
    
    # Test factory builds ApifyScraperClient when replay_from_cache is False
    monkeypatch.setenv("REPLAY_FROM_CACHE", "False")
    monkeypatch.setenv("APIFY_API_TOKEN", "dummy-apify-token")
    
    get_settings.cache_clear()
    
    service_live = build_ingestion_service()
    assert isinstance(service_live.scraper_client, ApifyScraperClient)
    assert service_live.write_to_cache is True

def test_fixture_loader():
    # Load linkedin jobs raw fixture
    records = load_fixture_scrape("linkedin_jobs")
    assert len(records) == 2
    assert records[0]["company"] == "  Acme   Corp. "
    assert records[0]["title"] == "Sénior Dev"
    assert records[1]["company"] == "Google"
    
    # Load indeed jobs raw fixture
    indeed_records = load_fixture_scrape("indeed_jobs")
    assert len(indeed_records) == 2
    assert indeed_records[0]["company"] == "Finance Corp"
    assert indeed_records[1]["company"] == "Meta"
