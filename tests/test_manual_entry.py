import asyncio
from datetime import datetime, timezone

import pytest

from config import get_settings
from coordinator import RunCoordinator, RunAlreadyActiveError
from domain import JobPosting, JobStatus, MatchResult, RunStatus
from ingestion import DedupService, NormalizeResult, ScrapeQuery
from knowledge import KnowledgeBase, KnowledgeLoadError
from persistence import init_db, PersistenceService
from skills import ScorerError
from ui.page import (
    build_view_state,
    add_manual_job_handler,
    ManualEntryValidationError,
    ManualEntryScoringError,
)


QUERY = ScrapeQuery(terms="Project Manager", location="Porto", limit=10)


@pytest.fixture(autouse=True)
def configure_test_env(tmp_path, monkeypatch):
    from persistence.ledger import GoogleSheetLedger, SyncSummary

    async def fake_sync(self, rows):
        return SyncSummary(
            rows_updated=0,
            rows_added=len(rows),
            user_columns_preserved=[],
            worksheet_created=False
        )

    monkeypatch.setattr(GoogleSheetLedger, "sync", fake_sync)
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "dummy-llm-key")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("RAW_SCRAPE_DIR", str(tmp_path / "raw_scrapes"))
    # Pin everything the run's budget check reads, so the developer's .env cannot move it.
    monkeypatch.setenv("REPLAY_FROM_CACHE", "False")
    monkeypatch.setenv("APIFY_ACTOR_START_USD", "0.01")
    monkeypatch.setenv("APIFY_RESULT_USD", "0.003")
    monkeypatch.setenv("SCRAPER_RESULTS_PER_LIMIT", "4.5")
    monkeypatch.setenv("RUN_BUDGET_CAP_USD", "2.00")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "manual_entry_test.db"
    init_db(str(path))
    return str(path)


# --- Hand-rolled fakes (no mocking library) ---

class FixedScorer:
    """Returns a fixed score and records which jobs it was asked to score."""

    def __init__(self, score_value=85):
        # NOTE: not named `score` -- that would shadow the score() method below.
        self.score_value = score_value
        self.calls = []

    async def score(self, job, knowledge, cost_accumulator=None):
        self.calls.append(job.identity_hash)
        if cost_accumulator is not None:
            cost_accumulator.add_llm_usage(input_tokens=100, output_tokens=10)
        return make_match(job.identity_hash, self.score_value)


class FailingScorer:
    def __init__(self):
        self.calls = []

    async def score(self, job, knowledge, cost_accumulator=None):
        self.calls.append(job.identity_hash)
        raise ScorerError("Model output is not valid JSON: boom")


class FakeKnowledgeLoader:
    def load(self):
        return KnowledgeBase(cv="cv", persona="persona", ats_criteria="criteria")


class MissingKnowledgeLoader:
    def load(self):
        raise KnowledgeLoadError("Missing required knowledge file: cv.md")


class EmptyIngestionService:
    async def fetch(self, queries):
        return []


class BlockingIngestionService:
    """Holds a run inside fetch() until released."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch(self, queries):
        self.started.set()
        await self.release.wait()
        return []


class EmptyNormalizer:
    def normalize(self, records):
        return NormalizeResult(job_postings=[], errors=[])


def make_match(identity_hash, score):
    return MatchResult(
        identity_hash=identity_hash,
        score=score,
        dimension_breakdown={"Technical role content": float(score)},
        match_reasons=[f"Score is {score}"],
        scored_at=datetime.now(timezone.utc)
    )


def make_posting(company="Acme", title="Project Manager", location="Porto", source="manual",
                 status=JobStatus.SCRAPED, description="Lead delivery of a product team.",
                 url="https://example.org/job"):
    return JobPosting(
        company=company,
        title=title,
        location=location,
        url=url,
        description=description,
        source=source,
        scraped_at=datetime.now(timezone.utc),
        status=status,
        identity_hash=""
    )


def make_coordinator(persistence, scorer=None, knowledge_loader=None, ingestion=None):
    return RunCoordinator(
        ingestion_service=ingestion or EmptyIngestionService(),
        normalizer=EmptyNormalizer(),
        dedup_service=DedupService(persistence),
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=knowledge_loader,
        score_threshold=60,
        source_name="apify_linkedin"
    )


# --- score_one ---

@pytest.mark.asyncio
async def test_score_one_scores_and_transitions_scraped_job(db_path):
    persistence = PersistenceService(db_path)
    job = make_posting()
    await persistence.upsert_job(job)
    scorer = FixedScorer(85)
    coordinator = make_coordinator(persistence, scorer, FakeKnowledgeLoader())

    match = await coordinator.score_one(job.identity_hash)

    assert match.score == 85
    assert scorer.calls == [job.identity_hash]
    assert (await persistence.get_job(job.identity_hash)).status == JobStatus.MATCHED
    assert (await persistence.get_match_result(job.identity_hash)).score == 85


@pytest.mark.asyncio
async def test_score_one_without_threshold_sets_matched_on_low_score(db_path):
    persistence = PersistenceService(db_path)
    job = make_posting()
    await persistence.upsert_job(job)
    coordinator = make_coordinator(persistence, FixedScorer(10), FakeKnowledgeLoader())

    match = await coordinator.score_one(job.identity_hash, respect_threshold=False)

    assert match.score == 10
    assert (await persistence.get_job(job.identity_hash)).status == JobStatus.MATCHED


@pytest.mark.asyncio
async def test_score_one_respecting_threshold_sets_no_match_on_low_score(db_path):
    persistence = PersistenceService(db_path)
    job = make_posting()
    await persistence.upsert_job(job)
    coordinator = make_coordinator(persistence, FixedScorer(10), FakeKnowledgeLoader())

    await coordinator.score_one(job.identity_hash, respect_threshold=True)

    assert (await persistence.get_job(job.identity_hash)).status == JobStatus.NO_MATCH


@pytest.mark.asyncio
async def test_score_one_on_nonexistent_hash_raises(db_path):
    persistence = PersistenceService(db_path)
    scorer = FixedScorer()
    coordinator = make_coordinator(persistence, scorer, FakeKnowledgeLoader())

    with pytest.raises(ValueError, match="does not exist"):
        await coordinator.score_one("no-such-hash")
    assert scorer.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    JobStatus.MATCHED,
    JobStatus.WRITTEN,
    JobStatus.APPLIED,
    JobStatus.REJECTED,
    JobStatus.NO_MATCH,
])
async def test_score_one_returns_stored_score_for_non_scraped_job(db_path, status):
    persistence = PersistenceService(db_path)
    job = make_posting(status=status)
    await persistence.upsert_job(job)
    await persistence.save_match_result(make_match(job.identity_hash, 77))
    scorer = FixedScorer(99)
    coordinator = make_coordinator(persistence, scorer, FakeKnowledgeLoader())

    match = await coordinator.score_one(job.identity_hash, respect_threshold=False)

    assert match.score == 77
    assert scorer.calls == []
    assert (await persistence.get_job(job.identity_hash)).status == status


@pytest.mark.asyncio
async def test_score_one_refuses_while_run_active(db_path):
    persistence = PersistenceService(db_path)
    job = make_posting()
    await persistence.upsert_job(job)
    scorer = FixedScorer()
    ingestion = BlockingIngestionService()
    coordinator = make_coordinator(persistence, scorer, FakeKnowledgeLoader(), ingestion)

    coordinator.start_run([QUERY])
    await asyncio.wait_for(ingestion.started.wait(), timeout=5)

    with pytest.raises(RunAlreadyActiveError):
        await coordinator.score_one(job.identity_hash)
    assert scorer.calls == []
    assert (await persistence.get_job(job.identity_hash)).status == JobStatus.SCRAPED

    ingestion.release.set()
    await coordinator.wait()


@pytest.mark.asyncio
async def test_score_one_knowledge_failure_raises_and_leaves_job_scraped(db_path):
    persistence = PersistenceService(db_path)
    job = make_posting()
    await persistence.upsert_job(job)
    scorer = FixedScorer()
    coordinator = make_coordinator(persistence, scorer, MissingKnowledgeLoader())

    with pytest.raises(KnowledgeLoadError):
        await coordinator.score_one(job.identity_hash)
    assert scorer.calls == []
    assert (await persistence.get_job(job.identity_hash)).status == JobStatus.SCRAPED
    assert await persistence.get_match_result(job.identity_hash) is None


@pytest.mark.asyncio
async def test_score_one_scorer_error_propagates_and_leaves_job_scraped(db_path):
    persistence = PersistenceService(db_path)
    job = make_posting()
    await persistence.upsert_job(job)
    coordinator = make_coordinator(persistence, FailingScorer(), FakeKnowledgeLoader())

    with pytest.raises(ScorerError):
        await coordinator.score_one(job.identity_hash)
    assert (await persistence.get_job(job.identity_hash)).status == JobStatus.SCRAPED
    assert await persistence.get_match_result(job.identity_hash) is None


@pytest.mark.asyncio
async def test_score_one_requires_scorer_and_knowledge_loader(db_path):
    persistence = PersistenceService(db_path)
    job = make_posting()
    await persistence.upsert_job(job)
    coordinator = make_coordinator(persistence, scorer=None, knowledge_loader=FakeKnowledgeLoader())

    with pytest.raises(RuntimeError, match="requires a scorer"):
        await coordinator.score_one(job.identity_hash)


# --- Manual postings and identity ---

@pytest.mark.asyncio
async def test_manual_posting_upserts_and_reads_back_with_source_manual(db_path):
    persistence = PersistenceService(db_path)
    job = make_posting(source="manual")

    await persistence.upsert_job(job)

    stored = await persistence.get_job(job.identity_hash)
    assert stored.source == "manual"
    assert stored.company == "Acme"
    assert stored.status == JobStatus.SCRAPED


def test_manual_and_scraped_postings_share_identity_hash():
    manual = make_posting(source="manual", url="https://example.org/a")
    scraped = make_posting(source="LinkedIn", url="https://linkedin.com/jobs/view/1")
    assert manual.identity_hash == scraped.identity_hash


@pytest.mark.asyncio
async def test_run_exempts_manual_jobs_from_threshold(db_path):
    persistence = PersistenceService(db_path)
    manual = make_posting(company="Acme", source="manual")
    scraped = make_posting(company="Beta", source="LinkedIn")
    await persistence.upsert_job(manual)
    await persistence.upsert_job(scraped)
    coordinator = make_coordinator(persistence, FixedScorer(10), FakeKnowledgeLoader())

    run = coordinator.start_run([QUERY])
    await coordinator.wait()

    assert run.status == RunStatus.DONE
    assert (await persistence.get_job(manual.identity_hash)).status == JobStatus.MATCHED
    assert (await persistence.get_job(scraped.identity_hash)).status == JobStatus.NO_MATCH
    assert run.n_matched == 1
    assert run.n_no_match == 1


# --- add_manual_job_handler ---

@pytest.mark.asyncio
@pytest.mark.parametrize("company, title", [
    ("", "Project Manager"),
    (None, "Project Manager"),
    ("Acme", "   "),
    ("!!!", "Project Manager"),
])
async def test_handler_rejects_missing_company_or_title_without_writing(db_path, company, title):
    persistence = PersistenceService(db_path)
    scorer = FixedScorer()
    coordinator = make_coordinator(persistence, scorer, FakeKnowledgeLoader())

    with pytest.raises(ManualEntryValidationError):
        await add_manual_job_handler(
            company=company, title=title, location="Porto", url="", description="d",
            persistence=persistence, coordinator=coordinator
        )
    assert await persistence.list_jobs() == []
    assert scorer.calls == []


@pytest.mark.asyncio
async def test_handler_refuses_during_active_run_without_writing(db_path):
    persistence = PersistenceService(db_path)
    scorer = FixedScorer()
    ingestion = BlockingIngestionService()
    coordinator = make_coordinator(persistence, scorer, FakeKnowledgeLoader(), ingestion)

    coordinator.start_run([QUERY])
    await asyncio.wait_for(ingestion.started.wait(), timeout=5)

    with pytest.raises(RunAlreadyActiveError):
        await add_manual_job_handler(
            company="Acme", title="Project Manager", location="Porto", url="", description="d",
            persistence=persistence, coordinator=coordinator
        )
    assert await persistence.list_jobs() == []
    assert scorer.calls == []

    ingestion.release.set()
    await coordinator.wait()


@pytest.mark.asyncio
async def test_handler_low_score_job_is_matched_and_renders_as_active_card(db_path):
    persistence = PersistenceService(db_path)
    coordinator = make_coordinator(persistence, FixedScorer(10), FakeKnowledgeLoader())

    result = await add_manual_job_handler(
        company="  Acme ", title="Project Manager", location="Porto",
        url="https://example.org/job", description="Lead delivery.",
        persistence=persistence, coordinator=coordinator
    )

    assert result.score == 10
    assert result.status == JobStatus.MATCHED
    assert result.rescored is True
    stored = await persistence.get_job(result.identity_hash)
    assert stored.source == "manual"
    assert stored.company == "Acme"

    vs = build_view_state(
        None,
        await persistence.list_jobs_with_match(),
        await persistence.counters(),
        await persistence.status_breakdown()
    )
    assert [m.identity_hash for m in vs.matches] == [result.identity_hash]
    assert vs.non_matches == []


@pytest.mark.asyncio
async def test_handler_scoring_failure_saves_job_as_scraped(db_path):
    persistence = PersistenceService(db_path)
    coordinator = make_coordinator(persistence, FailingScorer(), FakeKnowledgeLoader())

    with pytest.raises(ManualEntryScoringError, match="Saved, but scoring failed"):
        await add_manual_job_handler(
            company="Acme", title="Project Manager", location="Porto", url="", description="d",
            persistence=persistence, coordinator=coordinator
        )

    jobs = await persistence.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.SCRAPED
    assert jobs[0].source == "manual"


@pytest.mark.asyncio
async def test_handler_repaste_of_applied_job_keeps_status_and_stored_score(db_path):
    persistence = PersistenceService(db_path)
    original = make_posting(
        source="Indeed", status=JobStatus.APPLIED,
        description="Old description", url="https://indeed.example/1"
    )
    await persistence.upsert_job(original)
    await persistence.save_match_result(make_match(original.identity_hash, 77))
    scorer = FixedScorer(99)
    coordinator = make_coordinator(persistence, scorer, FakeKnowledgeLoader())

    result = await add_manual_job_handler(
        company="Acme", title="Project Manager", location="Porto",
        url="https://example.org/new", description="New description",
        persistence=persistence, coordinator=coordinator
    )

    assert result.identity_hash == original.identity_hash
    assert result.rescored is False
    assert result.score == 77
    assert result.status == JobStatus.APPLIED
    assert scorer.calls == []
    stored = await persistence.get_job(original.identity_hash)
    assert stored.source == "manual"
    assert stored.description == "New description"
    assert stored.url == "https://example.org/new"
