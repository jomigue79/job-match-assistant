import asyncio
from datetime import datetime, timezone
import pytest

from config import get_settings
from domain import Run, RunStatus, JobPosting, JobStatus
from ingestion import IngestionService, Normalizer, DedupService, ScrapeQuery, NormalizeResult, DedupResult, RawScrapeCache
from persistence import init_db, PersistenceService
from coordinator import RunCoordinator, RunAlreadyActiveError, build_run_coordinator
from helpers import load_fixture_scrape

@pytest.fixture(autouse=True)
def configure_test_env(tmp_path, monkeypatch):
    from persistence.ledger import GoogleSheetLedger, SyncSummary
    async def mock_sync(self, rows):
        return SyncSummary(
            rows_updated=0,
            rows_added=len(rows),
            user_columns_preserved=[],
            worksheet_created=False
        )
    monkeypatch.setattr(GoogleSheetLedger, "sync", mock_sync)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "coordinator_test.db"
    init_db(str(path))
    return str(path)

# Fake/Stub implementations for unit tests
class FakeIngestionService:
    def __init__(self, records=None):
        self.records = records or []
        self.fetch_called = False

    async def fetch(self, query):
        self.fetch_called = True
        return self.records

class FakeNormalizer:
    def __init__(self, postings=None, errors=None):
        self.postings = postings or []
        self.errors = errors or []

    def normalize(self, records):
        return NormalizeResult(
            job_postings=self.postings,
            errors=self.errors
        )

class SimpleFakeScorer:
    async def score(self, job, knowledge, cost_accumulator=None):
        from domain import MatchResult
        return MatchResult(
            identity_hash=job.identity_hash,
            score=80,
            dimension_breakdown={"Criteria": 8.0},
            match_reasons=["Meets criteria"],
            scored_at=datetime.now(timezone.utc)
        )

def build_job_posting(company: str, title: str, location: str, url: str) -> JobPosting:
    return JobPosting(
        company=company,
        title=title,
        location=location,
        url=url,
        description="Simple description",
        source="test_source",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.SCRAPED,
        identity_hash=""
    )

@pytest.mark.asyncio
async def test_immediate_return_and_completion(db_path):
    persistence = PersistenceService(db_path)
    
    jp1 = build_job_posting("Acme", "Developer", "Remote", "http://acme.org/1")
    
    fake_ingestion = FakeIngestionService([{"company": "Acme", "title": "Developer"}])
    fake_normalizer = FakeNormalizer([jp1], [])
    dedup = DedupService(persistence)
    
    coordinator = RunCoordinator(
        ingestion_service=fake_ingestion,
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        source_name="test_source"
    )
    
    # start_run returns immediately
    run = coordinator.start_run()
    assert run.status == RunStatus.RUNNING
    assert run.run_id is not None
    assert run.started_at is not None
    assert run.finished_at is None
    
    # Await the background pipeline
    await coordinator.wait()
    
    assert run.status == RunStatus.DONE
    assert run.finished_at is not None
    assert run.n_scraped == 1
    assert run.n_new == 1
    assert run.n_errors == 0
    
    # Verify job persisted
    persisted_job = await persistence.get_job(jp1.identity_hash)
    assert persisted_job is not None
    assert persisted_job.company == "Acme"
    
    # Verify run persisted
    persisted_run = await persistence.get_run(run.run_id)
    assert persisted_run is not None
    assert persisted_run.status == RunStatus.DONE
    assert persisted_run.n_scraped == 1
    assert persisted_run.n_new == 1

@pytest.mark.asyncio
async def test_double_trigger_lock_error(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    event = asyncio.Event()
    
    class DelayedIngestionService:
        async def fetch(self, query):
            await event.wait()
            return []
            
    fake_normalizer = FakeNormalizer([], [])
    
    coordinator = RunCoordinator(
        ingestion_service=DelayedIngestionService(),
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        source_name="test_source"
    )
    
    # Start first run
    run1 = coordinator.start_run()
    assert run1.status == RunStatus.RUNNING
    
    # Second start_run triggers RunAlreadyActiveError
    with pytest.raises(RunAlreadyActiveError):
        coordinator.start_run()
        
    # Release the pipeline
    event.set()
    await coordinator.wait()
    
    assert run1.status == RunStatus.DONE
    
    # A new run can now start successfully
    run2 = coordinator.start_run()
    assert run2.status == RunStatus.RUNNING
    
    # Clean up run2
    await coordinator.wait()

@pytest.mark.asyncio
async def test_end_to_end_fixture_replay(tmp_path, monkeypatch):
    # Setup directories
    db_file = tmp_path / "app.db"
    raw_dir = tmp_path / "raw_scrapes"
    raw_dir.mkdir()
    
    # Configure env settings
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    monkeypatch.setenv("RAW_SCRAPE_DIR", str(raw_dir))
    monkeypatch.setenv("REPLAY_FROM_CACHE", "True")
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "dummy-key")
    
    get_settings.cache_clear()
    settings = get_settings()
    
    # Initialize DB
    init_db(settings.db_path)
    
    # Write linkedin_jobs fixture to the raw scrapes cache
    cache = RawScrapeCache(settings.raw_scrape_dir)
    query = ScrapeQuery(terms="Software Engineer", location="Remote", limit=5)
    records = load_fixture_scrape("linkedin_jobs")
    cache.write(records, "apify_linkedin", query)
    
    # Use factory to build the coordinator
    coordinator = build_run_coordinator(scorer=SimpleFakeScorer())
    
    # Run
    run = coordinator.start_run()
    assert run.status == RunStatus.RUNNING
    
    await coordinator.wait()
    
    assert run.status == RunStatus.DONE
    assert run.n_scraped == 2
    assert run.n_new == 2
    assert run.n_errors == 0
    
    # Verify database persistence of jobs
    persistence = PersistenceService(settings.db_path)
    jobs = await persistence.list_jobs()
    assert len(jobs) == 2
    
    # Verify run row present
    persisted_run = await persistence.get_run(run.run_id)
    assert persisted_run is not None
    assert persisted_run.status == RunStatus.DONE
    assert persisted_run.n_scraped == 2
    assert persisted_run.n_new == 2

@pytest.mark.asyncio
async def test_idempotent_resumption(tmp_path, monkeypatch):
    db_file = tmp_path / "app.db"
    raw_dir = tmp_path / "raw_scrapes"
    raw_dir.mkdir()
    
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    monkeypatch.setenv("RAW_SCRAPE_DIR", str(raw_dir))
    monkeypatch.setenv("REPLAY_FROM_CACHE", "True")
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "dummy-key")
    
    get_settings.cache_clear()
    settings = get_settings()
    
    init_db(settings.db_path)
    persistence = PersistenceService(settings.db_path)
    
    # Pre-insert one of the fixture jobs (Acme Corp) using same fields
    acme_job = JobPosting(
        company="  Acme   Corp. ",
        title="Sénior Dev",
        location="Lisboa",
        url="https://linkedin.com/jobs/view/1",
        description="This is a great role for a Senior Developer in Lisbon.",
        source="apify_linkedin",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.SCRAPED,
        identity_hash=""
    )
    await persistence.upsert_job(acme_job)
    
    # Write fixture to cache
    cache = RawScrapeCache(settings.raw_scrape_dir)
    query = ScrapeQuery(terms="Software Engineer", location="Remote", limit=5)
    records = load_fixture_scrape("linkedin_jobs")
    cache.write(records, "apify_linkedin", query)
    
    coordinator = build_run_coordinator(persistence_service=persistence, scorer=SimpleFakeScorer())
    
    # Execute run
    run = coordinator.start_run()
    await coordinator.wait()
    
    assert run.status == RunStatus.DONE
    assert run.n_scraped == 2
    assert run.n_new == 1  # Only Google Staff Engineer is new
    
    # Assert total persisted jobs == 2 (no duplicates)
    all_jobs = await persistence.list_jobs()
    assert len(all_jobs) == 2
    
    # Assert hashes are correct
    hashes = {job.identity_hash for job in all_jobs}
    assert acme_job.identity_hash in hashes

@pytest.mark.asyncio
async def test_failure_path_handling(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    class FailingIngestionService:
        async def fetch(self, query):
            raise RuntimeError("Scraper actor exploded!")
            
    fake_normalizer = FakeNormalizer([], [])
    
    coordinator = RunCoordinator(
        ingestion_service=FailingIngestionService(),
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        source_name="test_source"
    )
    
    run = coordinator.start_run()
    await coordinator.wait()
    
    assert run.status == RunStatus.FAILED
    assert run.n_errors == 1
    
    # Verify run persisted as failed in DB
    persisted_run = await persistence.get_run(run.run_id)
    assert persisted_run is not None
    assert persisted_run.status == RunStatus.FAILED
    assert persisted_run.n_errors == 1
    
    # Verify we can run again (lock released)
    run2 = coordinator.start_run()
    assert run2.status == RunStatus.RUNNING
    await coordinator.wait()

@pytest.mark.asyncio
async def test_normalization_errors_recorded(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    records = [
        {
            "company": "Google",
            "title": "Staff Engineer",
            "location": "Remote",
            "url": "https://linkedin.com/jobs/view/2",
            "description": "Valid job description"
        },
        {
            "company": "", # empty company triggers NormalizationError
            "title": "Malformed Engineer",
            "location": "Remote",
            "url": "https://linkedin.com/jobs/view/3",
            "description": "Invalid job description"
        }
    ]
    
    fake_ingestion = FakeIngestionService(records)
    normalizer = Normalizer(source_name="apify_linkedin")
    
    coordinator = RunCoordinator(
        ingestion_service=fake_ingestion,
        normalizer=normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        source_name="apify_linkedin"
    )
    
    run = coordinator.start_run()
    await coordinator.wait()
    
    assert run.status == RunStatus.DONE
    assert run.n_scraped == 2
    assert run.n_errors == 1  # 1 malformed record skipped
    assert run.n_new == 1     # 1 valid job found and new
    
    # Verify only 1 job in database
    jobs = await persistence.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].company == "Google"
