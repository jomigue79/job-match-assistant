"""
A failed generation has to tell the user something.

On 2026-10-01 two regenerate attempts failed and the user saw nothing: the button spun
and returned, no new version, no message. The notification was raised in the slot of the
card that generation itself had just rebuilt, so ui.notify threw and the handler
swallowed it at WARNING.

Two fixes are covered here: the message goes through a host element the rebuild cannot
delete, and a writer failure reads differently from a crash. If a notification is still
lost, the text that was lost is logged at ERROR rather than discarded.
"""
import inspect
from datetime import datetime, timezone

import pytest

from domain import JobPosting, JobStatus, MatchResult
from knowledge import KnowledgeBase
from persistence import init_db, PersistenceService
from skills.writer import WriterError
from ui.page import WRITER_FAILURE_MESSAGE, build_ui, generate_cover_letter_handler

LETTER = "A generated letter long enough to clear the writer's minimum, with nothing of note in it."


class FakeWriter:
    def __init__(self, error: Exception = None):
        self.error = error
        self.instruction = None

    async def generate(self, job, knowledge, match_result, cost_accumulator=None, instruction=""):
        self.instruction = instruction
        if self.error is not None:
            raise self.error
        return LETTER


class FakeKnowledgeLoader:
    def load(self) -> KnowledgeBase:
        return KnowledgeBase(
            cv="CV", persona="Persona", ats_criteria="Criteria",
            letter_rules="rules", voice="voice",
        )


class RecordingNotifier:
    """Stands in for handle_error_notify, which takes a message and a type."""

    def __init__(self, raises: bool = False):
        self.calls = []
        self.raises = raises

    def __call__(self, message, kind="negative"):
        self.calls.append((message, kind))
        if self.raises:
            raise RuntimeError("The parent element this slot belongs to has been deleted.")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "letter_error_test.db"
    init_db(str(path))
    return str(path)


async def seed(persistence):
    job = JobPosting(
        company="Fake Co", title="Fake Role", location="Remote", url="http://example.com",
        description="desc", source="test", scraped_at=datetime.now(timezone.utc),
        status=JobStatus.MATCHED, identity_hash=""
    )
    await persistence.upsert_job(job)
    await persistence.save_match_result(MatchResult(
        identity_hash=job.identity_hash, score=85,
        dimension_breakdown={"overall": 85.0}, match_reasons=["reason"],
        scored_at=datetime.now(timezone.utc)
    ))
    return job


async def run_handler(db_path, error, notifier, instruction=""):
    persistence = PersistenceService(db_path)
    job = await seed(persistence)
    writer = FakeWriter(error)
    hashes = set()
    await generate_cover_letter_handler(
        identity_hash=job.identity_hash,
        persistence=persistence,
        writer=writer,
        knowledge_loader=FakeKnowledgeLoader(),
        generating_hashes=hashes,
        on_error_notify_callback=notifier,
        instruction=instruction,
    )
    return persistence, job, hashes, writer


# --- What the user is told ---

@pytest.mark.asyncio
async def test_writer_error_notifies_with_its_own_message(db_path):
    notifier = RecordingNotifier()
    await run_handler(db_path, WriterError("boom"), notifier)

    assert len(notifier.calls) == 1
    message, kind = notifier.calls[0]
    assert message == WRITER_FAILURE_MESSAGE
    assert "nothing was written" in message
    assert "letter_rules.md" in message
    assert kind == "warning"


@pytest.mark.asyncio
async def test_other_exceptions_keep_the_generic_message(db_path):
    notifier = RecordingNotifier()
    await run_handler(db_path, ValueError("something else broke"), notifier)

    message, kind = notifier.calls[0]
    assert message == "Error generating cover letter: something else broke"
    assert kind == "negative"


@pytest.mark.asyncio
async def test_lost_notification_is_logged_as_error(db_path, caplog):
    caplog.set_level("ERROR")
    notifier = RecordingNotifier(raises=True)
    await run_handler(db_path, WriterError("boom"), notifier)

    assert "Could not show the user why letter generation failed" in caplog.text
    assert "nothing was written" in caplog.text


# --- What the failure leaves behind ---

@pytest.mark.asyncio
async def test_failure_writes_no_letter_and_clears_the_generating_flag(db_path):
    notifier = RecordingNotifier()
    persistence, job, hashes, _ = await run_handler(db_path, WriterError("boom"), notifier)

    assert await persistence.get_latest_cover_letter(job.identity_hash) is None
    assert (await persistence.get_job(job.identity_hash)).status == JobStatus.MATCHED
    assert hashes == set()


@pytest.mark.asyncio
async def test_a_successful_generation_notifies_nothing(db_path):
    notifier = RecordingNotifier()
    persistence, job, _, _ = await run_handler(db_path, None, notifier)

    assert notifier.calls == []
    assert (await persistence.get_latest_cover_letter(job.identity_hash)).text == LETTER


# --- The one-off instruction reaches the writer and is not stored ---

@pytest.mark.asyncio
async def test_instruction_reaches_the_writer(db_path):
    _, _, _, writer = await run_handler(db_path, None, RecordingNotifier(), instruction="shorter")
    assert writer.instruction == "shorter"


@pytest.mark.asyncio
async def test_instruction_is_never_persisted(db_path):
    """Two generations, one with an instruction: the stored rows differ in nothing but text."""
    persistence = PersistenceService(db_path)
    job = await seed(persistence)

    for instruction in ("", "much shorter and name the clients"):
        await generate_cover_letter_handler(
            identity_hash=job.identity_hash,
            persistence=persistence,
            writer=FakeWriter(None),
            knowledge_loader=FakeKnowledgeLoader(),
            generating_hashes=set(),
            instruction=instruction,
        )

    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT text FROM cover_letters ORDER BY version;").fetchall()
        columns = [r[1] for r in conn.execute("PRAGMA table_info(cover_letters);")]
    finally:
        conn.close()

    assert [r[0] for r in rows] == [LETTER, LETTER]
    assert "instruction" not in columns
    for row in rows:
        assert "much shorter" not in row[0]


# --- Where the notification is raised from ---

def test_notify_host_is_created_outside_the_rebuilt_container():
    source = inspect.getsource(build_ui)

    assert 'notify_host = ui.element("div")' in source
    assert source.index('notify_host = ui.element("div")') < source.index("def rebuild_cards")

    notifier = source[source.index("def handle_error_notify"):]
    notifier = notifier[:notifier.index("async def generate_letter")]
    assert "with notify_host:" in notifier
    assert "lost_message" in notifier
