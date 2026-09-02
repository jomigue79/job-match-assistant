import asyncio
import contextlib
import pytest
from datetime import datetime, timezone

from config import get_settings
from domain import Run, RunStatus, JobPosting, JobStatus, MatchResult
from ingestion import NormalizeResult
from persistence import init_db, PersistenceService
from coordinator import RunCoordinator


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
    path = tmp_path / "coordinator_shutdown_test.db"
    init_db(str(path))
    return str(path)


# --- Hand-rolled fakes (no mocking library) ---

class FakeIngestionService:
    """Returns a fixed record list immediately."""

    def __init__(self, records=None):
        self.records = records or []

    async def fetch(self, query):
        return self.records


class BlockingIngestionService:
    """
    Blocks inside fetch() until released, so a run can be held mid-pipeline
    while shutdown() cancels it. Signals when it has actually started.
    """

    def __init__(self):
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.cancelled = False

    async def fetch(self, query):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return []


class FakeNormalizer:
    def __init__(self, postings=None, errors=None):
        self.postings = postings or []
        self.errors = errors or []

    def normalize(self, records):
        return NormalizeResult(job_postings=self.postings, errors=self.errors)


class FakeDedupService:
    """Passes every posting through as new."""

    def __init__(self, new_jobs=None):
        self.new_jobs = new_jobs

    async def filter_new(self, postings):
        from ingestion import DedupResult
        jobs = self.new_jobs if self.new_jobs is not None else postings
        return DedupResult(
            new_jobs=jobs,
            n_total_in=len(postings),
            n_intra_batch_duplicates=0,
            n_already_seen=0,
            n_new=len(jobs)
        )


class FakeKnowledgeLoader:
    def load(self):
        from knowledge import KnowledgeBase
        return KnowledgeBase(cv="cv", persona="persona", ats_criteria="criteria")


class BlockingScorer:
    """
    Scores the first job immediately, then blocks forever on the second.
    Lets a test cancel a run after some jobs are already persisted.
    """

    def __init__(self, block_after=1, score_value=85):
        self.block_after = block_after
        # NOTE: not named `score` -- that would shadow the score() method below.
        self.score_value = score_value
        self.call_count = 0
        self.reached_block = asyncio.Event()
        self._never = asyncio.Event()

    async def score(self, job, knowledge, cost_accumulator=None):
        self.call_count += 1
        if self.call_count > self.block_after:
            self.reached_block.set()
            await self._never.wait()
        return MatchResult(
            identity_hash=job.identity_hash,
            score=self.score_value,
            dimension_breakdown={"Criteria": float(self.score_value)},
            match_reasons=[f"Score is {self.score_value}"],
            scored_at=datetime.now(timezone.utc)
        )


def make_posting(identity_hash, title="Engineer", company="Acme"):
    return JobPosting(
        company=company,
        title=title,
        location="Remote",
        url=f"https://example.test/{identity_hash}",
        description="A description.",
        source="test_source",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.SCRAPED,
        identity_hash=identity_hash
    )


async def wait_until(predicate, timeout=5.0, interval=0.02):
    """
    Polls an async predicate until it returns True. Raises TimeoutError otherwise.
    Used instead of a bare sleep so the tests do not race the pipeline's writes.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("Condition not met within timeout")


def build_coordinator(persistence, ingestion=None, scorer=None, knowledge_loader=None, postings=None):
    return RunCoordinator(
        ingestion_service=ingestion if ingestion is not None else FakeIngestionService(),
        normalizer=FakeNormalizer(postings or []),
        dedup_service=FakeDedupService(),
        persistence_service=persistence,
        scorer=scorer,
        knowledge_loader=knowledge_loader,
        source_name="test_source"
    )


# --- Tests ---

@pytest.mark.asyncio
async def test_shutdown_no_active_task_is_noop(db_path):
    """shutdown() on a fresh coordinator returns without raising and writes no run row."""
    persistence = PersistenceService(db_path)
    coordinator = build_coordinator(persistence)

    assert coordinator._active_task is None

    await coordinator.shutdown()

    assert await persistence.list_recent_runs(limit=10) == []


@pytest.mark.asyncio
async def test_shutdown_completed_task_is_noop(db_path):
    """
    A finished run keeps its terminal status when shutdown() is called afterwards.

    shutdown() returns early on task.done(), so this asserts the early return
    rather than any status-preservation logic. A task that completes between the
    done() check and the write is a known unhandled race, accepted for a
    one-second shutdown path.
    """
    persistence = PersistenceService(db_path)
    coordinator = build_coordinator(persistence)

    run = coordinator.start_run()
    await coordinator.wait()

    assert coordinator._active_task.done()
    before = await persistence.get_run(run.run_id)
    assert before.status == RunStatus.DONE

    await coordinator.shutdown()

    after = await persistence.get_run(run.run_id)
    assert after.status == RunStatus.DONE
    assert after.finished_at == before.finished_at


@pytest.mark.asyncio
async def test_shutdown_active_run_without_task(db_path):
    """
    shutdown() returns without raising and writes nothing when _active_run is set
    but _active_task is None.

    Unreachable after 1365977, where start_run() assigns both together. This test
    guards the reordering: if a future change constructs the Run before acquiring
    the loop, this state becomes reachable again.
    """
    persistence = PersistenceService(db_path)
    coordinator = build_coordinator(persistence)

    coordinator._active_run = Run(
        run_id="orphan-run",
        source="test_source",
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc)
    )
    coordinator._active_task = None

    await coordinator.shutdown()

    assert await persistence.get_run("orphan-run") is None
    assert coordinator._active_run.status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_shutdown_cancels_in_flight_run(db_path):
    """shutdown() cancels the in-flight task; the task reports itself as cancelled."""
    persistence = PersistenceService(db_path)
    ingestion = BlockingIngestionService()
    coordinator = build_coordinator(persistence, ingestion=ingestion)

    coordinator.start_run()
    await ingestion.started.wait()

    await coordinator.shutdown()

    assert coordinator._active_task.cancelled()
    assert ingestion.cancelled is True


@pytest.mark.asyncio
async def test_shutdown_persists_failed_status(db_path):
    """
    After cancelling an in-flight run, the persisted row reads failed with a
    non-null finished_at. This is acceptance criterion 5 asserted in-process.

    On 1365977 this fails with AttributeError, because shutdown() is new in this
    branch -- it does not reach an assertion at all. It proves the new API
    behaves, not that the old behaviour was broken. The DONE-vs-FAILED defect is
    asserted substantively by test_cancelled_run_persists_as_failed_not_done.
    """
    persistence = PersistenceService(db_path)
    ingestion = BlockingIngestionService()
    coordinator = build_coordinator(persistence, ingestion=ingestion)

    run = coordinator.start_run()
    await ingestion.started.wait()

    await coordinator.shutdown()

    stored = await persistence.get_run(run.run_id)
    assert stored is not None
    assert stored.status == RunStatus.FAILED
    assert stored.finished_at is not None
    assert stored.n_errors >= 1


@pytest.mark.asyncio
async def test_shutdown_is_idempotent(db_path):
    """Calling shutdown() twice raises nothing and does not re-stamp the row."""
    persistence = PersistenceService(db_path)
    ingestion = BlockingIngestionService()
    coordinator = build_coordinator(persistence, ingestion=ingestion)

    run = coordinator.start_run()
    await ingestion.started.wait()

    await coordinator.shutdown()
    first = await persistence.get_run(run.run_id)

    await coordinator.shutdown()
    second = await persistence.get_run(run.run_id)

    assert second.status == RunStatus.FAILED
    assert second.finished_at == first.finished_at
    assert second.n_errors == first.n_errors


@pytest.mark.asyncio
async def test_shutdown_never_raises_on_persistence_failure(db_path):
    """
    A failing save_run is logged and swallowed; shutdown() still returns.

    Note on what this actually exercises: _execute_run's own finally calls
    save_run first, so the injected RuntimeError surfaces out of `await task`
    inside shutdown() -- it is not suppressed by contextlib.suppress, which only
    catches CancelledError -- and is caught by shutdown()'s except Exception.
    A consequence worth knowing: when the finally's write raises, shutdown()
    never reaches its own write, so no failed row is recorded. The process still
    exits cleanly and init_db's crash-recovery sweep repairs the row on the next
    launch. The contract asserted here is only that shutdown() does not raise.
    """
    persistence = PersistenceService(db_path)
    ingestion = BlockingIngestionService()
    coordinator = build_coordinator(persistence, ingestion=ingestion)

    coordinator.start_run()
    await ingestion.started.wait()

    calls = {"count": 0}
    original_save_run = persistence.save_run

    async def exploding_save_run(run):
        calls["count"] += 1
        raise RuntimeError("simulated database failure")

    persistence.save_run = exploding_save_run
    try:
        await coordinator.shutdown()
    finally:
        persistence.save_run = original_save_run

    assert calls["count"] >= 1
    assert coordinator._active_task.done()


@pytest.mark.asyncio
async def test_cancelled_run_not_recorded_as_done(db_path):
    """
    A run cancelled via shutdown() persists as failed, never as done.

    On 1365977 this fails with AttributeError, because shutdown() is new in this
    branch -- it never reaches the status assertion. It covers the shutdown path
    specifically; the underlying DONE-vs-FAILED defect in _execute_run is
    asserted substantively by test_cancelled_run_persists_as_failed_not_done,
    which drives cancellation directly and needs no new API.
    """
    persistence = PersistenceService(db_path)
    ingestion = BlockingIngestionService()
    coordinator = build_coordinator(persistence, ingestion=ingestion)

    run = coordinator.start_run()
    await ingestion.started.wait()

    await coordinator.shutdown()

    stored = await persistence.get_run(run.run_id)
    assert stored.status != RunStatus.DONE
    assert stored.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_execute_run_reraises_cancelled_error(db_path):
    """
    _execute_run re-raises CancelledError rather than swallowing it, so the
    canceller can distinguish 'cancelled' from 'completed normally'.
    """
    persistence = PersistenceService(db_path)
    ingestion = BlockingIngestionService()
    coordinator = build_coordinator(persistence, ingestion=ingestion)

    coordinator.start_run()
    await ingestion.started.wait()

    task = coordinator._active_task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancelled_run_persists_as_failed_not_done(db_path):
    """
    Isolates the `except asyncio.CancelledError` branch in _execute_run.

    Cancellation is driven directly through the task, never through shutdown(),
    so this test exercises only code that exists on 1365977 and fails there on a
    substantive assertion rather than an AttributeError:

      CancelledError is a BaseException, so `except Exception` did not catch it.
      Control fell through to the finally, where run.status was still RUNNING
      and n_errors was 0, so `DONE if n_errors == 0 else FAILED` evaluated to
      DONE. A cancelled run recorded itself as successful.

    The finally block still runs during cancellation and performs the write, so
    the row exists either way -- only its status differs.
    """
    persistence = PersistenceService(db_path)
    ingestion = BlockingIngestionService()
    coordinator = build_coordinator(persistence, ingestion=ingestion)

    run = coordinator.start_run()
    await ingestion.started.wait()

    task = coordinator._active_task
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    stored = await persistence.get_run(run.run_id)
    assert stored is not None, "the finally block should still have written the row"
    assert stored.status != RunStatus.DONE
    assert stored.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_shutdown_leaves_scored_jobs_intact(db_path):
    """
    Jobs scored before cancellation keep their scores and statuses; jobs not
    reached stay scraped for the next run to pick up.
    """
    persistence = PersistenceService(db_path)
    postings = [make_posting("hash_a", title="First"), make_posting("hash_b", title="Second")]
    scorer = BlockingScorer(block_after=1, score_value=85)
    coordinator = build_coordinator(
        persistence,
        ingestion=FakeIngestionService(records=[{"a": 1}, {"b": 2}]),
        scorer=scorer,
        knowledge_loader=FakeKnowledgeLoader(),
        postings=postings
    )

    coordinator.start_run()
    await scorer.reached_block.wait()

    # reached_block fires as soon as the second job enters the scorer, which can
    # be before the first job's match result and status transition are committed.
    # Wait for that write to land so the test asserts survival of a persisted
    # score rather than racing the pipeline.
    async def first_job_committed():
        jobs = await persistence.list_jobs()
        return any(j.status in (JobStatus.MATCHED, JobStatus.NO_MATCH) for j in jobs)

    await wait_until(first_job_committed)

    await coordinator.shutdown()

    statuses = {}
    for job in await persistence.list_jobs():
        statuses[job.identity_hash] = job.status

    scored = [h for h, s in statuses.items() if s in (JobStatus.MATCHED, JobStatus.NO_MATCH)]
    unscored = [h for h, s in statuses.items() if s == JobStatus.SCRAPED]

    assert len(scored) == 1
    assert len(unscored) == 1

    match = await persistence.get_match_result(scored[0])
    assert match is not None
    assert match.score == 85

    assert await persistence.get_match_result(unscored[0]) is None
