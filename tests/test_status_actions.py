import pytest
from datetime import datetime, timezone
from domain import (
    JobPosting,
    JobStatus,
    InvalidStateTransitionError,
    validate_transition,
    MatchResult
)
from persistence import init_db, PersistenceService, JobWithMatch, Counters
from ui.page import build_view_state, MatchCard
from coordinator import RunCoordinator
from ingestion import RawScrapeCache, ScrapeQuery

class FakeScorer:
    def __init__(self):
        self.called_with = []

    async def score(self, job, knowledge, cost_accumulator=None):
        self.called_with.append(job.identity_hash)
        return MatchResult(
            identity_hash=job.identity_hash,
            score=85,
            dimension_breakdown={"overall": 85.0},
            match_reasons=["R"],
            scored_at=datetime.now(timezone.utc)
        )

class FakeIngestionService:
    async def fetch(self, query):
        return []

class FakeNormalizer:
    def normalize(self, records):
        from ingestion.normalization import NormalizationResult
        return NormalizationResult(job_postings=[], errors=[])

class FakeDedupService:
    def __init__(self):
        pass
    async def filter_new(self, postings):
        from ingestion import DedupResult
        return DedupResult(
            new_jobs=[],
            n_total_in=0,
            n_intra_batch_duplicates=0,
            n_already_seen=0,
            n_new=0
        )

class FakeKnowledgeLoader:
    def load(self):
        from knowledge import KnowledgeBase
        return KnowledgeBase(cv="CV", persona="Persona", ats_criteria="Criteria")

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "status_actions_test.db"
    init_db(str(path))
    return str(path)

def make_job_posting(identity_hash="h1", status=JobStatus.SCRAPED):
    return JobPosting(
        company="Fake Co",
        title="Fake Engineer",
        location="Remote",
        url="http://example.com",
        description="desc",
        source="test",
        scraped_at=datetime.now(timezone.utc),
        status=status,
        identity_hash=identity_hash
    )

def make_match_result(job_hash):
    return MatchResult(
        identity_hash=job_hash,
        score=85,
        dimension_breakdown={"overall": 85.0},
        match_reasons=["Reason"],
        scored_at=datetime.now(timezone.utc)
    )

# --- Test 1 & 2: Status transitions (valid and invalid) ---

@pytest.mark.parametrize(
    "from_status, to_status",
    [
        (JobStatus.MATCHED, JobStatus.WRITTEN),
        (JobStatus.MATCHED, JobStatus.APPLIED),
        (JobStatus.MATCHED, JobStatus.REJECTED),
        (JobStatus.WRITTEN, JobStatus.APPLIED),
        (JobStatus.WRITTEN, JobStatus.REJECTED),
        (JobStatus.APPLIED, JobStatus.REJECTED),
        (JobStatus.REJECTED, JobStatus.MATCHED),
    ]
)
def test_valid_transitions(from_status, to_status):
    # Should not raise exception
    validate_transition(from_status, to_status)

@pytest.mark.parametrize(
    "from_status, to_status",
    [
        (JobStatus.APPLIED, JobStatus.MATCHED),
        (JobStatus.APPLIED, JobStatus.WRITTEN),
        (JobStatus.REJECTED, JobStatus.WRITTEN),
        (JobStatus.REJECTED, JobStatus.APPLIED),
        (JobStatus.NO_MATCH, JobStatus.MATCHED),
        (JobStatus.NO_MATCH, JobStatus.WRITTEN),
        (JobStatus.SCRAPED, JobStatus.APPLIED),
        (JobStatus.SCRAPED, JobStatus.REJECTED),
    ]
)
def test_invalid_transitions(from_status, to_status):
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(from_status, to_status)

# --- Test 3 & 4 & 7: UI / Handler level state updates and counters ---

@pytest.mark.asyncio
async def test_apply_and_reject_handlers(db_path):
    persistence = PersistenceService(db_path)
    job = make_job_posting(status=JobStatus.MATCHED)
    await persistence.upsert_job(job)
    
    # 1. Transition matched -> applied
    await persistence.set_status(job.identity_hash, JobStatus.APPLIED)
    updated_job = await persistence.get_job(job.identity_hash)
    assert updated_job.status == JobStatus.APPLIED
    
    cnt = await persistence.counters()
    assert cnt.applied == 1
    assert cnt.rejected == 0
    
    # 2. Transition applied -> rejected
    await persistence.set_status(job.identity_hash, JobStatus.REJECTED)
    updated_job = await persistence.get_job(job.identity_hash)
    assert updated_job.status == JobStatus.REJECTED
    
    cnt2 = await persistence.counters()
    assert cnt2.applied == 0
    assert cnt2.rejected == 1
    
    # 3. Transition rejected -> matched (Undo)
    await persistence.set_status(job.identity_hash, JobStatus.MATCHED)
    updated_job = await persistence.get_job(job.identity_hash)
    assert updated_job.status == JobStatus.MATCHED
    
    cnt3 = await persistence.counters()
    assert cnt3.applied == 0
    assert cnt3.rejected == 0

# --- Test 5: Coordinator skip guard ---

@pytest.mark.asyncio
async def test_coordinator_scoring_skip_guard(db_path):
    persistence = persistence = PersistenceService(db_path)
    
    # Seed three actioned jobs: one applied, one rejected, one written
    job_applied = make_job_posting(identity_hash="h_app", status=JobStatus.APPLIED)
    job_rejected = make_job_posting(identity_hash="h_rej", status=JobStatus.REJECTED)
    job_written = make_job_posting(identity_hash="h_wri", status=JobStatus.WRITTEN)
    job_scraped = make_job_posting(identity_hash="h_scr", status=JobStatus.SCRAPED)
    
    await persistence.upsert_job(job_applied)
    await persistence.upsert_job(job_rejected)
    await persistence.upsert_job(job_written)
    await persistence.upsert_job(job_scraped)
    
    # Fake scorer and coordinator
    scorer = FakeScorer()
    coordinator = RunCoordinator(
        ingestion_service=FakeIngestionService(),
        normalizer=FakeNormalizer(),
        dedup_service=FakeDedupService(),
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=FakeKnowledgeLoader()
    )
    
    # Note: run coordinator scans SCRAPED status jobs in _execute_run scoring phase,
    # but we can call _score_and_persist directly or run the coordinator logic.
    # To test the skip guard in _score_and_persist, we pass it all jobs.
    run = coordinator.start_run()
    await coordinator.wait()
    
    # Call _score_and_persist manually with the seeded jobs to verify guard
    await coordinator._score_and_persist(job_applied, None, None, run)
    await coordinator._score_and_persist(job_rejected, None, None, run)
    await coordinator._score_and_persist(job_written, None, None, run)
    await coordinator._score_and_persist(job_scraped, None, None, run)
    
    # Assert scorer was ONLY called for the scraped job
    assert scorer.called_with == ["h_scr"]
    
    # Assert statuses remain unchanged
    j_app = await persistence.get_job("h_app")
    assert j_app.status == JobStatus.APPLIED
    j_rej = await persistence.get_job("h_rej")
    assert j_rej.status == JobStatus.REJECTED
    j_wri = await persistence.get_job("h_wri")
    assert j_wri.status == JobStatus.WRITTEN

# --- Test 6: build_view_state grouping ---

def test_build_view_state_grouping():
    j_match = JobPosting(company="C1", title="T1", location="L1", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.MATCHED, identity_hash="h1")
    j_applied = JobPosting(company="C2", title="T2", location="L2", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.APPLIED, identity_hash="h2")
    j_rejected = JobPosting(company="C3", title="T3", location="L3", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.REJECTED, identity_hash="h3")
    
    mr1 = make_match_result("h1")
    mr2 = make_match_result("h2")
    mr3 = make_match_result("h3")
    
    jobs_with_match = [
        JobWithMatch(job=j_match, match=mr1),
        JobWithMatch(job=j_applied, match=mr2),
        JobWithMatch(job=j_rejected, match=mr3),
    ]
    
    counters = Counters(total=3, rejected=1, written=0, applied=1)
    status_breakdown = {
        JobStatus.MATCHED: 1,
        JobStatus.APPLIED: 1,
        JobStatus.REJECTED: 1
    }
    
    vs = build_view_state(None, jobs_with_match, counters, status_breakdown)
    
    assert len(vs.matches) == 1
    assert vs.matches[0].identity_hash == "h1"
    
    assert len(vs.applied) == 1
    assert vs.applied[0].identity_hash == "h2"
    assert vs.applied[0].status == JobStatus.APPLIED
    
    assert len(vs.rejected) == 1
    assert vs.rejected[0].identity_hash == "h3"
    assert vs.rejected[0].status == JobStatus.REJECTED
