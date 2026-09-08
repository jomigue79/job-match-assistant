"""
Ordering of vs.applied, produced by build_view_state.

letter_created_at is a proxy for the application date -- there is no applied_at
column. Applied cards sort newest letter first, with None last. Nothing else in
the suite constructs more than one APPLIED job, so this file is the only cover
for that ordering.

Hand-rolled fixtures only; no mocking library.
"""
from datetime import datetime, timezone

from domain import JobPosting, JobStatus, MatchResult
from persistence import Counters, JobWithMatch
from ui.page import build_view_state


def make_match_result(identity_hash: str, score: int = 80) -> MatchResult:
    return MatchResult(
        identity_hash=identity_hash,
        score=score,
        dimension_breakdown={"overall": float(score)},
        match_reasons=[f"Score is {score}"],
        scored_at=datetime.now(timezone.utc),
    )


def make_applied(identity_hash: str, company: str, letter_created_at):
    """
    An APPLIED job with a match result. A letter is attached only when a
    timestamp is given, mirroring how list_jobs_with_match populates the row.
    """
    job = JobPosting(
        company=company,
        title=f"Title {identity_hash}",
        location="Porto",
        source="test_source",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.APPLIED,
        identity_hash=identity_hash,
    )
    has_letter = letter_created_at is not None
    return JobWithMatch(
        job=job,
        match=make_match_result(identity_hash),
        letter_text="Dear hiring manager..." if has_letter else None,
        letter_version=1 if has_letter else None,
        letter_created_at=letter_created_at,
    )


def view_state_for(rows):
    """build_view_state with counters and breakdown consistent with the rows."""
    counters = Counters(total=len(rows), rejected=0, written=0, applied=len(rows))
    status_breakdown = {JobStatus.APPLIED: len(rows)}
    return build_view_state(None, rows, counters, status_breakdown)


# --- Two dated letters: newest first ---

def test_two_applied_sorted_newest_letter_first():
    older = datetime(2026, 6, 14, 8, 40, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 13, 16, 30, tzinfo=timezone.utc)

    # Deliberately supplied oldest-first so a no-op would fail the assertion.
    rows = [
        make_applied("h_old", "Older Co", older),
        make_applied("h_new", "Newer Co", newer),
    ]

    vs = view_state_for(rows)

    assert len(vs.applied) == 2
    assert [c.identity_hash for c in vs.applied] == ["h_new", "h_old"]
    assert vs.applied[0].letter_created_at == newer
    assert vs.applied[1].letter_created_at == older


def test_three_applied_strict_descending():
    d1 = datetime(2026, 1, 5, tzinfo=timezone.utc)
    d2 = datetime(2026, 5, 5, tzinfo=timezone.utc)
    d3 = datetime(2026, 9, 5, tzinfo=timezone.utc)

    rows = [
        make_applied("h_mid", "Mid Co", d2),
        make_applied("h_first", "First Co", d1),
        make_applied("h_last", "Last Co", d3),
    ]

    vs = view_state_for(rows)

    dates = [c.letter_created_at for c in vs.applied]
    assert dates == sorted(dates, reverse=True)
    assert [c.identity_hash for c in vs.applied] == ["h_last", "h_mid", "h_first"]


# --- None sorts last ---

def test_applied_without_letter_sorts_last():
    dated = datetime(2026, 7, 13, 16, 30, tzinfo=timezone.utc)

    # The undated row is supplied first; it must end up last.
    rows = [
        make_applied("h_none", "No Letter Co", None),
        make_applied("h_dated", "Dated Co", dated),
    ]

    vs = view_state_for(rows)

    assert [c.identity_hash for c in vs.applied] == ["h_dated", "h_none"]
    assert vs.applied[-1].letter_created_at is None
    assert vs.applied[-1].has_letter is False
    assert vs.applied[0].has_letter is True


def test_none_sorts_after_even_the_oldest_letter():
    """A None must lose to every real date, including one at the epoch floor."""
    ancient = datetime(1970, 1, 1, tzinfo=timezone.utc)

    rows = [
        make_applied("h_none", "No Letter Co", None),
        make_applied("h_ancient", "Ancient Co", ancient),
    ]

    vs = view_state_for(rows)

    assert [c.identity_hash for c in vs.applied] == ["h_ancient", "h_none"]


def test_mixed_dated_and_undated_keeps_dated_block_ordered():
    d_old = datetime(2026, 2, 2, tzinfo=timezone.utc)
    d_new = datetime(2026, 8, 8, tzinfo=timezone.utc)

    rows = [
        make_applied("h_none_a", "None A", None),
        make_applied("h_old", "Old Co", d_old),
        make_applied("h_none_b", "None B", None),
        make_applied("h_new", "New Co", d_new),
    ]

    vs = view_state_for(rows)

    order = [c.identity_hash for c in vs.applied]
    assert order[:2] == ["h_new", "h_old"]
    assert set(order[2:]) == {"h_none_a", "h_none_b"}
    assert all(c.letter_created_at is None for c in vs.applied[2:])


# --- All None: no TypeError, stable order ---

def test_all_none_does_not_raise_and_is_stable():
    """
    Comparing datetime against None raises TypeError. The sort substitutes an
    epoch floor precisely to avoid that. With every row undated the sort must
    complete and preserve input order.
    """
    rows = [
        make_applied("h_1", "First Co", None),
        make_applied("h_2", "Second Co", None),
        make_applied("h_3", "Third Co", None),
    ]

    vs = view_state_for(rows)

    assert len(vs.applied) == 3
    assert [c.identity_hash for c in vs.applied] == ["h_1", "h_2", "h_3"]
    assert all(c.letter_created_at is None for c in vs.applied)


def test_all_none_is_deterministic_across_calls():
    rows = [
        make_applied("h_a", "A Co", None),
        make_applied("h_b", "B Co", None),
    ]

    first = [c.identity_hash for c in view_state_for(rows).applied]
    second = [c.identity_hash for c in view_state_for(rows).applied]

    assert first == second == ["h_a", "h_b"]


# --- The sort must not disturb the other buckets ---

def test_sort_does_not_affect_other_buckets():
    applied_row = make_applied("h_app", "Applied Co", datetime(2026, 3, 3, tzinfo=timezone.utc))

    matched_job = JobPosting(
        company="Matched Co", title="T", location="Porto", source="s",
        scraped_at=datetime.now(timezone.utc), status=JobStatus.MATCHED,
        identity_hash="h_match",
    )
    rejected_job = JobPosting(
        company="Rejected Co", title="T", location="Porto", source="s",
        scraped_at=datetime.now(timezone.utc), status=JobStatus.REJECTED,
        identity_hash="h_rej",
    )

    rows = [
        applied_row,
        JobWithMatch(job=matched_job, match=make_match_result("h_match")),
        JobWithMatch(job=rejected_job, match=make_match_result("h_rej")),
    ]

    counters = Counters(total=3, rejected=1, written=0, applied=1)
    status_breakdown = {
        JobStatus.APPLIED: 1,
        JobStatus.MATCHED: 1,
        JobStatus.REJECTED: 1,
    }
    vs = build_view_state(None, rows, counters, status_breakdown)

    assert [c.identity_hash for c in vs.applied] == ["h_app"]
    assert [c.identity_hash for c in vs.matches] == ["h_match"]
    assert [c.identity_hash for c in vs.rejected] == ["h_rej"]
    assert vs.non_matches == []
    assert vs.pending == []
