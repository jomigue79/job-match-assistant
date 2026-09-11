import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest

from config import get_settings
from domain import JobPosting, JobStatus, MatchResult
from persistence import init_db, PersistenceService
from ui.page import build_view_state, save_letter_edit_handler, LetterEditValidationError


@pytest.fixture(autouse=True)
def configure_test_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "dummy-llm-key")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("RAW_SCRAPE_DIR", str(tmp_path / "raw_scrapes"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "edit_letter_test.db"
    init_db(str(path))
    return str(path)


def make_posting(company="Acme", title="Project Manager", status=JobStatus.WRITTEN):
    return JobPosting(
        company=company,
        title=title,
        location="Porto",
        url="https://example.org/job",
        description="Lead delivery of a product team.",
        source="manual",
        scraped_at=datetime.now(timezone.utc),
        status=status,
        identity_hash=""
    )


def make_match(identity_hash, score=80):
    return MatchResult(
        identity_hash=identity_hash,
        score=score,
        dimension_breakdown={"Technical role content": float(score)},
        match_reasons=[f"Score is {score}"],
        scored_at=datetime.now(timezone.utc)
    )


async def seed_job_with_letter(persistence, text="Dear Acme, original letter.", **posting_kwargs):
    job = make_posting(**posting_kwargs)
    await persistence.upsert_job(job)
    letter = await persistence.save_cover_letter(job.identity_hash, text)
    return job, letter


def stored_text(db_path, identity_hash, version):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT text FROM cover_letters WHERE identity_hash = ? AND version = ?;",
            (identity_hash, version)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# --- update_cover_letter ---

@pytest.mark.asyncio
async def test_update_changes_text_and_keeps_version_and_created_at(db_path):
    persistence = PersistenceService(db_path)
    job, original = await seed_job_with_letter(persistence)

    updated = await persistence.update_cover_letter(job.identity_hash, "Dear Acme, edited letter.")

    latest = await persistence.get_latest_cover_letter(job.identity_hash)
    assert latest.text == "Dear Acme, edited letter."
    assert latest.version == original.version == 1
    assert latest.created_at == original.created_at
    assert updated.text == "Dear Acme, edited letter."
    assert updated.version == 1
    assert updated.created_at == original.created_at


@pytest.mark.asyncio
async def test_update_targets_only_the_highest_version(db_path):
    persistence = PersistenceService(db_path)
    job, _ = await seed_job_with_letter(persistence, text="version one")
    await persistence.save_cover_letter(job.identity_hash, "version two")

    await persistence.update_cover_letter(job.identity_hash, "version two, edited")

    assert stored_text(db_path, job.identity_hash, 1) == "version one"
    assert stored_text(db_path, job.identity_hash, 2) == "version two, edited"


@pytest.mark.asyncio
async def test_update_without_a_letter_raises(db_path):
    persistence = PersistenceService(db_path)
    job = make_posting()
    await persistence.upsert_job(job)

    with pytest.raises(ValueError, match="No cover letter exists"):
        await persistence.update_cover_letter(job.identity_hash, "Dear Acme")


@pytest.mark.asyncio
async def test_update_for_unknown_job_raises(db_path):
    persistence = PersistenceService(db_path)

    with pytest.raises(ValueError, match="No cover letter exists"):
        await persistence.update_cover_letter("no-such-hash", "Dear Acme")


@pytest.mark.asyncio
async def test_stale_expected_version_raises_and_writes_nothing(db_path):
    persistence = PersistenceService(db_path)
    job, _ = await seed_job_with_letter(persistence, text="version one")
    # A regeneration lands while a dialog opened on v1 is still open.
    await persistence.save_cover_letter(job.identity_hash, "regenerated version two")

    with pytest.raises(ValueError, match="regenerated"):
        await persistence.update_cover_letter(job.identity_hash, "edited version one", expected_version=1)

    assert stored_text(db_path, job.identity_hash, 1) == "version one"
    assert stored_text(db_path, job.identity_hash, 2) == "regenerated version two"


@pytest.mark.asyncio
async def test_matching_expected_version_updates(db_path):
    persistence = PersistenceService(db_path)
    job, _ = await seed_job_with_letter(persistence)

    await persistence.update_cover_letter(job.identity_hash, "edited", expected_version=1)

    assert stored_text(db_path, job.identity_hash, 1) == "edited"


@pytest.mark.asyncio
async def test_update_that_matches_no_row_raises_and_reports_nothing_updated(db_path):
    persistence = PersistenceService(db_path)
    job, _ = await seed_job_with_letter(persistence, text="untouched")
    # Stands in for a row removed by another connection between the SELECT and the
    # UPDATE: RAISE(IGNORE) makes the UPDATE match the row but change nothing.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TRIGGER skip_letter_update BEFORE UPDATE ON cover_letters "
            "BEGIN SELECT RAISE(IGNORE); END;"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match="no longer exists"):
        await persistence.update_cover_letter(job.identity_hash, "edited")

    assert stored_text(db_path, job.identity_hash, 1) == "untouched"


@pytest.mark.asyncio
async def test_list_jobs_with_match_returns_edited_text(db_path):
    persistence = PersistenceService(db_path)
    job, _ = await seed_job_with_letter(persistence)
    await persistence.save_match_result(make_match(job.identity_hash))

    await persistence.update_cover_letter(job.identity_hash, "Dear Acme, edited letter.")

    (jm,) = await persistence.list_jobs_with_match()
    assert jm.letter_text == "Dear Acme, edited letter."
    assert jm.letter_version == 1


@pytest.mark.asyncio
async def test_regenerate_after_edit_creates_a_new_version(db_path):
    persistence = PersistenceService(db_path)
    job, _ = await seed_job_with_letter(persistence, text="original")
    await persistence.update_cover_letter(job.identity_hash, "edited")

    regenerated = await persistence.save_cover_letter(job.identity_hash, "regenerated")

    latest = await persistence.get_latest_cover_letter(job.identity_hash)
    assert regenerated.version == 2
    assert latest.version == 2
    assert latest.text == "regenerated"
    assert stored_text(db_path, job.identity_hash, 1) == "edited"


@pytest.mark.asyncio
async def test_edit_does_not_change_applied_ordering(db_path):
    persistence = PersistenceService(db_path)
    older, _ = await seed_job_with_letter(persistence, text="older letter", company="Older Co", status=JobStatus.APPLIED)
    await asyncio.sleep(0.01)
    newer, _ = await seed_job_with_letter(persistence, text="newer letter", company="Newer Co", status=JobStatus.APPLIED)
    await persistence.save_match_result(make_match(older.identity_hash))
    await persistence.save_match_result(make_match(newer.identity_hash))

    async def applied_order():
        vs = build_view_state(
            None,
            await persistence.list_jobs_with_match(),
            await persistence.counters(),
            await persistence.status_breakdown()
        )
        return [c.identity_hash for c in vs.applied]

    before = await applied_order()
    await persistence.update_cover_letter(older.identity_hash, "older letter, edited")
    after = await applied_order()

    assert before == [newer.identity_hash, older.identity_hash]
    assert after == before


# --- save_letter_edit_handler ---

@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "\n\t", None])
async def test_handler_rejects_empty_text_without_writing(db_path, text):
    persistence = PersistenceService(db_path)
    job, _ = await seed_job_with_letter(persistence, text="untouched")

    with pytest.raises(LetterEditValidationError):
        await save_letter_edit_handler(job.identity_hash, text, persistence)

    assert stored_text(db_path, job.identity_hash, 1) == "untouched"


@pytest.mark.asyncio
async def test_handler_saves_text_exactly_as_typed(db_path):
    persistence = PersistenceService(db_path)
    job, _ = await seed_job_with_letter(persistence)
    typed = "  Dear Acme,\n\nEdited paragraph.\n"

    result = await save_letter_edit_handler(job.identity_hash, typed, persistence, expected_version=1)

    assert result.text == typed
    assert stored_text(db_path, job.identity_hash, 1) == typed
