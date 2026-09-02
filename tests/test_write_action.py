import pytest
from datetime import datetime, timezone

from domain import JobPosting, JobStatus, MatchResult
from knowledge import KnowledgeBase
from persistence import init_db, PersistenceService, JobWithMatch, Counters
from skills.writer import WriterError
from ui.page import build_view_state, generate_cover_letter_handler

class FakeWriter:
    def __init__(self, response_text: str = "", should_raise: bool = False):
        self.response_text = response_text
        self.should_raise = should_raise
        self.called_with = None

    async def generate(self, job, knowledge, match_result, cost_accumulator = None):
        self.called_with = (job, knowledge, match_result)
        if self.should_raise:
            raise WriterError("Simulated generation failure")
        return self.response_text

class FakeKnowledgeLoader:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def load(self) -> KnowledgeBase:
        return self.kb

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "write_action_test.db"
    init_db(str(path))
    return str(path)

def make_job_posting():
    return JobPosting(
        company="Fake Co",
        title="Fake Engineer",
        location="Remote",
        url="http://example.com",
        description="desc",
        source="test",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.SCRAPED,
        identity_hash=""
    )

def make_knowledge_base():
    return KnowledgeBase(
        cv="CV Text",
        persona="Persona Text",
        ats_criteria="Criteria Text"
    )

def make_match_result(job_hash):
    return MatchResult(
        identity_hash=job_hash,
        score=85,
        dimension_breakdown={"overall": 85},
        match_reasons=["Reason 1"],
        scored_at=datetime.now(timezone.utc)
    )

# A sample 120 character string to pass the length check
SAMPLE_LONG_RESPONSE = "Dear Hiring Manager, I am writing to express my strong interest in the Software Engineer position. With over ten years of dedicated Python engineering, I possess the required qualifications. I look forward to contributing."

@pytest.mark.asyncio
async def test_write_action_success(db_path):
    persistence = PersistenceService(db_path)
    job = make_job_posting()
    await persistence.upsert_job(job)
    await persistence.set_status(job.identity_hash, JobStatus.MATCHED)
    
    mr = make_match_result(job.identity_hash)
    await persistence.save_match_result(mr)
    
    kb = make_knowledge_base()
    loader = FakeKnowledgeLoader(kb)
    writer = FakeWriter(response_text=SAMPLE_LONG_RESPONSE)
    
    generating_hashes = set()
    
    await generate_cover_letter_handler(
        identity_hash=job.identity_hash,
        persistence=persistence,
        writer=writer,
        knowledge_loader=loader,
        generating_hashes=generating_hashes
    )
    
    # Assert status updated to written
    updated_job = await persistence.get_job(job.identity_hash)
    assert updated_job.status == JobStatus.WRITTEN
    
    # Assert cover letter saved
    latest_cl = await persistence.get_latest_cover_letter(job.identity_hash)
    assert latest_cl is not None
    assert latest_cl.version == 1
    assert "Dear Hiring Manager" in latest_cl.text

@pytest.mark.asyncio
async def test_write_action_regenerate_increments_version(db_path):
    persistence = PersistenceService(db_path)
    job = make_job_posting()
    await persistence.upsert_job(job)
    await persistence.set_status(job.identity_hash, JobStatus.MATCHED)
    
    mr = make_match_result(job.identity_hash)
    await persistence.save_match_result(mr)
    
    kb = make_knowledge_base()
    loader = FakeKnowledgeLoader(kb)
    writer_1 = FakeWriter(response_text="Generated letter text version 1 of at least 100 characters long to pass validations.")
    writer_2 = FakeWriter(response_text="Generated letter text version 2 of at least 100 characters long to pass validations.")
    
    generating_hashes = set()
    errors = []
    
    # First generate
    await generate_cover_letter_handler(
        identity_hash=job.identity_hash,
        persistence=persistence,
        writer=writer_1,
        knowledge_loader=loader,
        generating_hashes=generating_hashes,
        on_error_notify_callback=lambda msg: errors.append(msg)
    )
    
    # Second generate (Regenerate)
    await generate_cover_letter_handler(
        identity_hash=job.identity_hash,
        persistence=persistence,
        writer=writer_2,
        knowledge_loader=loader,
        generating_hashes=generating_hashes,
        on_error_notify_callback=lambda msg: errors.append(msg)
    )
    
    # Assert no errors occurred
    assert len(errors) == 0
    
    # Assert latest cover letter version is 2
    latest_cl = await persistence.get_latest_cover_letter(job.identity_hash)
    assert latest_cl is not None
    assert latest_cl.version == 2
    assert "version 2" in latest_cl.text

@pytest.mark.asyncio
async def test_write_action_writer_error_preserves_status(db_path):
    persistence = PersistenceService(db_path)
    job = make_job_posting()
    await persistence.upsert_job(job)
    await persistence.set_status(job.identity_hash, JobStatus.MATCHED)
    
    mr = make_match_result(job.identity_hash)
    await persistence.save_match_result(mr)
    
    kb = make_knowledge_base()
    loader = FakeKnowledgeLoader(kb)
    writer = FakeWriter(should_raise=True)
    
    generating_hashes = set()
    errors = []
    
    await generate_cover_letter_handler(
        identity_hash=job.identity_hash,
        persistence=persistence,
        writer=writer,
        knowledge_loader=loader,
        generating_hashes=generating_hashes,
        on_error_notify_callback=lambda msg: errors.append(msg)
    )
    
    # Assert status remains MATCHED
    updated_job = await persistence.get_job(job.identity_hash)
    assert updated_job.status == JobStatus.MATCHED
    
    # Assert no letter saved
    latest_cl = await persistence.get_latest_cover_letter(job.identity_hash)
    assert latest_cl is None
    
    # Assert error notified
    assert len(errors) == 1
    assert "Simulated generation failure" in errors[0]

@pytest.mark.asyncio
async def test_build_view_state_with_and_without_letter():
    job_no_letter = JobPosting(company="C1", title="T1", location="L1", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.MATCHED, identity_hash="h1")
    job_with_letter = JobPosting(company="C2", title="T2", location="L2", source="s", scraped_at=datetime.now(timezone.utc), status=JobStatus.WRITTEN, identity_hash="h2")
    
    mr1 = MatchResult(identity_hash="h1", score=85, dimension_breakdown={}, match_reasons=["R1"], scored_at=datetime.now(timezone.utc))
    mr2 = MatchResult(identity_hash="h2", score=90, dimension_breakdown={}, match_reasons=["R2"], scored_at=datetime.now(timezone.utc))
    
    now = datetime.now(timezone.utc)
    jobs_with_match = [
        JobWithMatch(job=job_no_letter, match=mr1, letter_text=None, letter_version=None, letter_created_at=None),
        JobWithMatch(job=job_with_letter, match=mr2, letter_text="Cover letter text", letter_version=1, letter_created_at=now)
    ]
    
    counters = Counters(total=2, rejected=0, written=1, applied=0)
    status_breakdown = {JobStatus.MATCHED: 1, JobStatus.WRITTEN: 1}
    
    vs = build_view_state(None, jobs_with_match, counters, status_breakdown)
    
    # Assert vs.matches count is 2
    assert len(vs.matches) == 2
    
    match_no_letter = next(m for m in vs.matches if m.identity_hash == "h1")
    assert match_no_letter.has_letter is False
    assert match_no_letter.letter_text is None
    
    match_with_letter = next(m for m in vs.matches if m.identity_hash == "h2")
    assert match_with_letter.has_letter is True
    assert match_with_letter.letter_text == "Cover letter text"
    assert match_with_letter.letter_version == 1
    assert match_with_letter.letter_created_at == now

@pytest.mark.asyncio
async def test_counter_update_on_written(db_path):
    persistence = PersistenceService(db_path)
    job = make_job_posting()
    await persistence.upsert_job(job)
    await persistence.set_status(job.identity_hash, JobStatus.MATCHED)
    
    # Before writing, count is 0
    cnt_before = await persistence.counters()
    assert cnt_before.written == 0
    
    # Write cover letter and transition status to written
    await persistence.save_cover_letter(job.identity_hash, "Generated letter text of at least 100 characters long to pass validations.")
    await persistence.set_status(job.identity_hash, JobStatus.WRITTEN)
    
    cnt_after = await persistence.counters()
    assert cnt_after.written == 1

@pytest.mark.asyncio
async def test_write_action_notify_runtime_error_does_not_crash(db_path, monkeypatch, caplog):
    import nicegui
    
    def mock_notify(*args, **kwargs):
        raise RuntimeError("The parent element this slot belongs to has been deleted")
        
    monkeypatch.setattr(nicegui.ui, "notify", mock_notify)
    
    persistence = PersistenceService(db_path)
    job = make_job_posting()
    await persistence.upsert_job(job)
    await persistence.set_status(job.identity_hash, JobStatus.MATCHED)
    
    mr = make_match_result(job.identity_hash)
    await persistence.save_match_result(mr)
    
    kb = make_knowledge_base()
    loader = FakeKnowledgeLoader(kb)
    writer = FakeWriter(should_raise=True)
    
    generating_hashes = set()
    
    # 1. Test fallback ui.notify raises RuntimeError
    await generate_cover_letter_handler(
        identity_hash=job.identity_hash,
        persistence=persistence,
        writer=writer,
        knowledge_loader=loader,
        generating_hashes=generating_hashes
    )
    
    # Assert hash is discarded
    assert job.identity_hash not in generating_hashes
    
    # Assert it logged a warning
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("Default notification raised RuntimeError" in w or "parent element" in w for w in warnings)
    
    # Reset state
    generating_hashes.clear()
    caplog.clear()
    
    # 2. Test when on_error_notify_callback is provided but raises RuntimeError
    def bad_callback(msg):
        raise RuntimeError("Slot deleted")
        
    await generate_cover_letter_handler(
        identity_hash=job.identity_hash,
        persistence=persistence,
        writer=writer,
        knowledge_loader=loader,
        generating_hashes=generating_hashes,
        on_error_notify_callback=bad_callback
    )
    
    assert job.identity_hash not in generating_hashes
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("Error callback notification raised RuntimeError" in w or "deleted" in w for w in warnings)
