import pytest
from datetime import datetime, timezone
from typing import Set, Iterable

from domain import JobPosting, JobStatus
from ingestion import DedupService, DedupResult
from persistence import init_db, PersistenceService

class StubHashesProvider:
    """
    In-memory mock/stub hashes provider to decouple DedupService tests.
    """
    def __init__(self, existing_hashes_set: Set[str]):
        self.existing_hashes_set = existing_hashes_set

    async def existing_hashes(self, hashes: Iterable[str]) -> Set[str]:
        return self.existing_hashes_set.intersection(hashes)

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "dedup_test.db"
    init_db(str(path))
    return str(path)

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
async def test_dedup_empty_input():
    provider = StubHashesProvider(set())
    service = DedupService(provider)
    
    result = await service.filter_new([])
    
    assert result.new_jobs == []
    assert result.n_total_in == 0
    assert result.n_intra_batch_duplicates == 0
    assert result.n_already_seen == 0
    assert result.n_new == 0

@pytest.mark.asyncio
async def test_dedup_intra_batch_collisions():
    provider = StubHashesProvider(set())
    service = DedupService(provider)
    
    jp1 = build_job_posting("Acme", "Developer", "Remote", "http://acme.org/1")
    jp2 = build_job_posting("Acme", "Developer", "Remote", "http://acme.org/2") # identical company/title/location -> same identity_hash
    
    assert jp1.identity_hash == jp2.identity_hash
    
    result = await service.filter_new([jp1, jp2])
    
    assert result.new_jobs == [jp1]
    assert result.n_total_in == 2
    assert result.n_intra_batch_duplicates == 1
    assert result.n_already_seen == 0
    assert result.n_new == 1

@pytest.mark.asyncio
async def test_dedup_ledger_stub_exclusion():
    jp1 = build_job_posting("Company A", "Role A", "Loc A", "http://a.com")
    jp2 = build_job_posting("Company B", "Role B", "Loc B", "http://b.com")
    
    provider = StubHashesProvider({jp1.identity_hash})
    service = DedupService(provider)
    
    result = await service.filter_new([jp1, jp2])
    
    assert result.new_jobs == [jp2]
    assert result.n_total_in == 2
    assert result.n_intra_batch_duplicates == 0
    assert result.n_already_seen == 1
    assert result.n_new == 1

@pytest.mark.asyncio
async def test_dedup_all_seen():
    jp1 = build_job_posting("Company A", "Role A", "Loc A", "http://a.com")
    jp2 = build_job_posting("Company B", "Role B", "Loc B", "http://b.com")
    
    provider = StubHashesProvider({jp1.identity_hash, jp2.identity_hash})
    service = DedupService(provider)
    
    result = await service.filter_new([jp1, jp2])
    
    assert result.new_jobs == []
    assert result.n_total_in == 2
    assert result.n_intra_batch_duplicates == 0
    assert result.n_already_seen == 2
    assert result.n_new == 0

@pytest.mark.asyncio
async def test_dedup_mixed_batch_invariant():
    jp1 = build_job_posting("Company A", "Role A", "Loc A", "http://a.com") # new
    jp2 = build_job_posting("Company A", "Role A", "Loc A", "http://a-dup.com") # intra-batch duplicate of jp1
    jp3 = build_job_posting("Company B", "Role B", "Loc B", "http://b.com") # already in ledger
    jp4 = build_job_posting("Company C", "Role C", "Loc C", "http://c.com") # new
    
    provider = StubHashesProvider({jp3.identity_hash})
    service = DedupService(provider)
    
    result = await service.filter_new([jp1, jp2, jp3, jp4])
    
    assert result.new_jobs == [jp1, jp4]
    assert result.n_total_in == 4
    assert result.n_intra_batch_duplicates == 1
    assert result.n_already_seen == 1
    assert result.n_new == 2
    
    # Invariant: 4 == 2 + 1 + 1
    assert result.n_total_in == result.n_new + result.n_intra_batch_duplicates + result.n_already_seen

@pytest.mark.asyncio
async def test_dedup_ledger_integration(db_path):
    persistence = PersistenceService(db_path)
    
    # Ingest a job directly into persistence
    jp_existing = build_job_posting("Company A", "Role A", "Loc A", "http://a.com")
    await persistence.upsert_job(jp_existing)
    
    # New job that is not in persistence
    jp_new = build_job_posting("Company B", "Role B", "Loc B", "http://b.com")
    
    # Scraped batch contains the existing job and the new job
    batch = [jp_existing, jp_new]
    
    # Dedup service pointing to the live persistence service
    service = DedupService(persistence)
    
    result = await service.filter_new(batch)
    
    assert result.new_jobs == [jp_new]
    assert result.n_total_in == 2
    assert result.n_intra_batch_duplicates == 0
    assert result.n_already_seen == 1
    assert result.n_new == 1
