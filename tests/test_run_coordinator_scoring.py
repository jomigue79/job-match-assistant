import asyncio
import pytest
from datetime import datetime, timezone

from config import get_settings
from domain import Run, RunStatus, JobPosting, JobStatus, RunCost
from ingestion import IngestionService, Normalizer, DedupService, ScrapeQuery, NormalizeResult
from persistence import init_db, PersistenceService
from coordinator import RunCoordinator, RunAlreadyActiveError, build_run_coordinator
from skills.scorer import ScorerError
from llm import LLMError
from knowledge import KnowledgeBase

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
    path = tmp_path / "coordinator_scoring_test.db"
    init_db(str(path))
    return str(path)

# Mock classes
class FakeIngestionService:
    def __init__(self, records=None):
        self.records = records or []

    async def fetch(self, query):
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

class ControlledFakeScorer:
    def __init__(self, scores_map=None, default_score=80, should_fail_hashes=None):
        self.scores_map = scores_map or {}
        self.default_score = default_score
        self.should_fail_hashes = should_fail_hashes or set()
        self.call_count = 0
        self.recorded_requests = []

    async def score(self, job, knowledge, cost_accumulator=None):
        self.call_count += 1
        self.recorded_requests.append((job, knowledge))
        
        if cost_accumulator is not None:
            cost_accumulator.add_llm_usage(input_tokens=100, output_tokens=50, calls=1)
            
        if job.identity_hash in self.should_fail_hashes:
            raise ScorerError(f"Simulated failure for job: {job.title}")

        score = self.scores_map.get(job.identity_hash, self.default_score)
        
        from domain import MatchResult
        return MatchResult(
            identity_hash=job.identity_hash,
            score=score,
            dimension_breakdown={"Criteria": float(score)},
            match_reasons=[f"Score is {score}"],
            scored_at=datetime.now(timezone.utc)
        )

class FakeKnowledgeLoader:
    def __init__(self, should_raise=False):
        self.should_raise = should_raise
        self.load_called = False

    def load(self):
        self.load_called = True
        if self.should_raise:
            raise ValueError("Failed to load knowledge files")
        return KnowledgeBase(
            cv="Fake CV",
            persona="Fake Persona",
            ats_criteria="Fake ATS Criteria"
        )

def build_test_job_posting(company: str, title: str, location: str, url: str) -> JobPosting:
    return JobPosting(
        company=company,
        title=title,
        location=location,
        url=url,
        description="Write Python code and test it.",
        source="test_source",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.SCRAPED,
        identity_hash=""
    )

@pytest.mark.asyncio
async def test_end_to_end_scoring_transitions(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    jp1 = build_test_job_posting("Acme", "Developer", "Remote", "http://acme.org/1")
    jp2 = build_test_job_posting("Google", "Engineer", "Remote", "http://google.com/1")
    
    fake_ingestion = FakeIngestionService([{"title": "Developer"}, {"title": "Engineer"}])
    fake_normalizer = FakeNormalizer([jp1, jp2], [])
    
    # Threshold is 70
    # jp1 will score 70 -> matched
    # jp2 will score 69 -> no_match
    scorer = ControlledFakeScorer(
        scores_map={
            jp1.identity_hash: 70,
            jp2.identity_hash: 69
        }
    )
    kl = FakeKnowledgeLoader()
    
    coordinator = RunCoordinator(
        ingestion_service=fake_ingestion,
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=kl,
        score_threshold=70,
        source_name="test_source"
    )
    
    run = coordinator.start_run()
    await coordinator.wait()
    
    assert run.status == RunStatus.DONE
    assert run.n_matched == 1
    assert run.n_no_match == 1
    assert run.n_errors == 0
    
    # Assert database status changes
    job1 = await persistence.get_job(jp1.identity_hash)
    job2 = await persistence.get_job(jp2.identity_hash)
    assert job1.status == JobStatus.MATCHED
    assert job2.status == JobStatus.NO_MATCH
    
    # Assert MatchResult persisted
    mr1 = await persistence.get_match_result(jp1.identity_hash)
    mr2 = await persistence.get_match_result(jp2.identity_hash)
    assert mr1.score == 70
    assert mr2.score == 69

@pytest.mark.asyncio
async def test_scoring_concurrency_safety(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    # Pre-insert 15 scraped jobs
    jobs = []
    for i in range(15):
        job = build_test_job_posting(f"Company {i}", f"Job {i}", "Remote", f"http://example.com/{i}")
        await persistence.upsert_job(job)
        jobs.append(job)
        
    fake_ingestion = FakeIngestionService([])
    fake_normalizer = FakeNormalizer([], [])
    scorer = ControlledFakeScorer(default_score=80)
    kl = FakeKnowledgeLoader()
    
    coordinator = RunCoordinator(
        ingestion_service=fake_ingestion,
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=kl,
        score_threshold=70,
        source_name="test_source"
    )
    
    run = coordinator.start_run()
    await coordinator.wait()
    
    assert run.status == RunStatus.DONE
    assert run.n_matched == 15
    assert run.n_no_match == 0
    assert scorer.call_count == 15
    
    # Verify all MatchResults exist
    for job in jobs:
        mr = await persistence.get_match_result(job.identity_hash)
        assert mr is not None
        assert mr.score == 80

@pytest.mark.asyncio
async def test_re_run_skips_already_scored(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    jp1 = build_test_job_posting("Acme", "Developer", "Remote", "http://acme.org/1")
    
    fake_ingestion = FakeIngestionService([{"title": "Developer"}])
    fake_normalizer = FakeNormalizer([jp1], [])
    scorer = ControlledFakeScorer(default_score=80)
    kl = FakeKnowledgeLoader()
    
    coordinator = RunCoordinator(
        ingestion_service=fake_ingestion,
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=kl,
        score_threshold=70,
        source_name="test_source"
    )
    
    # Run once
    run1 = coordinator.start_run()
    await coordinator.wait()
    assert run1.n_new == 1
    assert scorer.call_count == 1
    
    # Run again on same fixture
    run2 = coordinator.start_run()
    await coordinator.wait()
    
    # n_new is 0 (deduplicated)
    assert run2.n_new == 0
    # scorer call count is still 1 (no re-scoring)
    assert scorer.call_count == 1

@pytest.mark.asyncio
async def test_resumption_scraped_jobs(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    # Pre-insert a scraped job
    scraped_job = build_test_job_posting("Old Corp", "Old Dev", "Remote", "http://old.com")
    await persistence.upsert_job(scraped_job)
    
    # Run triggers with 1 NEW job
    new_job = build_test_job_posting("New Corp", "New Dev", "Remote", "http://new.com")
    fake_ingestion = FakeIngestionService([{"title": "New Dev"}])
    fake_normalizer = FakeNormalizer([new_job], [])
    scorer = ControlledFakeScorer(default_score=85)
    kl = FakeKnowledgeLoader()
    
    coordinator = RunCoordinator(
        ingestion_service=fake_ingestion,
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=kl,
        score_threshold=70,
        source_name="test_source"
    )
    
    run = coordinator.start_run()
    await coordinator.wait()
    
    assert run.status == RunStatus.DONE
    # 2 jobs should be scored total (the pre-existing scraped one + new one)
    assert run.n_new == 1
    assert run.n_matched == 2
    assert scorer.call_count == 2

@pytest.mark.asyncio
async def test_per_job_scoring_failure(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    jp1 = build_test_job_posting("Good Co", "Good Dev", "Remote", "http://good.com")
    jp2 = build_test_job_posting("Bad Co", "Bad Dev", "Remote", "http://bad.com")
    
    fake_ingestion = FakeIngestionService([{"title": "Good Dev"}, {"title": "Bad Dev"}])
    fake_normalizer = FakeNormalizer([jp1, jp2], [])
    
    # Scorer will raise ScorerError on jp2
    scorer = ControlledFakeScorer(
        default_score=80,
        should_fail_hashes={jp2.identity_hash}
    )
    kl = FakeKnowledgeLoader()
    
    coordinator = RunCoordinator(
        ingestion_service=fake_ingestion,
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=kl,
        score_threshold=70,
        source_name="test_source"
    )
    
    run = coordinator.start_run()
    await coordinator.wait()
    
    # Run still completes as DONE (not FAILED)
    assert run.status == RunStatus.DONE
    assert run.n_matched == 1
    assert run.n_errors == 1 #jp2 failed
    
    # jp1 is matched, jp2 remains scraped
    job1 = await persistence.get_job(jp1.identity_hash)
    job2 = await persistence.get_job(jp2.identity_hash)
    assert job1.status == JobStatus.MATCHED
    assert job2.status == JobStatus.SCRAPED

@pytest.mark.asyncio
async def test_knowledge_load_failure(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    jp1 = build_test_job_posting("Acme", "Developer", "Remote", "http://acme.org/1")
    fake_ingestion = FakeIngestionService([{"title": "Developer"}])
    fake_normalizer = FakeNormalizer([jp1], [])
    scorer = ControlledFakeScorer()
    
    # Knowledge loader raises exception
    kl = FakeKnowledgeLoader(should_raise=True)
    
    coordinator = RunCoordinator(
        ingestion_service=fake_ingestion,
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=kl,
        score_threshold=70,
        source_name="test_source"
    )
    
    run = coordinator.start_run()
    await coordinator.wait()
    
    # Whole run fails immediately
    assert run.status == RunStatus.FAILED
    assert run.n_errors == 1
    assert kl.load_called is True
    
    # Job remains scraped (never reached scorer)
    job = await persistence.get_job(jp1.identity_hash)
    assert job.status == JobStatus.SCRAPED
    assert scorer.call_count == 0

@pytest.mark.asyncio
async def test_run_cost_mapping(db_path):
    persistence = PersistenceService(db_path)
    dedup = DedupService(persistence)
    
    jp1 = build_test_job_posting("Acme", "Developer", "Remote", "http://acme.org/1")
    jp2 = build_test_job_posting("Google", "Engineer", "Remote", "http://google.com/1")
    
    fake_ingestion = FakeIngestionService([{"title": "Developer"}, {"title": "Engineer"}])
    fake_normalizer = FakeNormalizer([jp1, jp2], [])
    
    # ControlledFakeScorer adds 100 input & 50 output tokens per score
    scorer = ControlledFakeScorer()
    kl = FakeKnowledgeLoader()
    
    coordinator = RunCoordinator(
        ingestion_service=fake_ingestion,
        normalizer=fake_normalizer,
        dedup_service=dedup,
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=kl,
        score_threshold=70,
        source_name="test_source"
    )
    
    run = coordinator.start_run()
    await coordinator.wait()
    
    # Assert cost metrics updated in Run
    assert run.cost.total_input_tokens == 200
    assert run.cost.total_output_tokens == 100
    assert run.cost.total_llm_calls == 2
