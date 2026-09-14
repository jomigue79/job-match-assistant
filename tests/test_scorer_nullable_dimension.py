"""
AI literacy & development may be null when the posting does not involve AI,
automation or development work. The code leaves a null dimension out and
redistributes its weight across the others, so a job is not capped at 85 for
lacking a property it was never meant to have. Every other dimension must be a
number.

Expected scores are hand-calculated and written as literals, not recomputed from
DIMENSION_WEIGHTS -- a test that derives its expectation from the code under test
would pass even if the arithmetic were wrong.

Hand-rolled fakes only; no mocking library.
"""
import itertools
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from config import get_settings
from domain import JobPosting, JobStatus, MatchResult
from knowledge import KnowledgeBase
from llm import LLMRequest, LLMResponse, LLMUsage
from persistence import init_db, PersistenceService, Counters, JobWithMatch
from skills.scorer import Scorer, ScorerError, SYSTEM_PROMPT, compute_score
from ui.page import build_view_state, format_dimension_value

AI = "AI literacy & development"

DIMENSION_KEYS = [
    "Technical role content",
    "Requirements coverage",
    AI,
    "Seniority & scope",
    "Domain & context",
]

NON_NULLABLE_KEYS = [k for k in DIMENSION_KEYS if k != AI]


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
    path = tmp_path / "nullable_dimension_test.db"
    init_db(str(path))
    return str(path)


class FakeLLMClient:
    def __init__(self, response_text: str):
        self.response_text = response_text
        self.last_request = None

    async def complete(self, request: LLMRequest, cost_accumulator=None) -> LLMResponse:
        self.last_request = request
        return LLMResponse(
            text=self.response_text,
            usage=LLMUsage(input_tokens=10, output_tokens=20),
            model="fake-model",
        )


def make_job_posting(status=JobStatus.SCRAPED):
    return JobPosting(
        company="Test Company",
        title="Project Manager",
        location="Porto",
        url="https://example.org/job",
        description="Coordinate delivery across departments.",
        source="test_source",
        scraped_at=datetime.now(timezone.utc),
        status=status,
    )


def make_knowledge_base():
    return KnowledgeBase(
        cv="My CV details.",
        persona="Write in a friendly tone.",
        ats_criteria="Evaluation criteria.",
    )


def dims(technical, requirements, ai, seniority, domain):
    return dict(zip(DIMENSION_KEYS, [technical, requirements, ai, seniority, domain]))


def response_for(dimensions):
    return json.dumps({
        "dimensions": dimensions,
        "reasons": ["A reason for each dimension."],
    })


async def score_with(dimensions):
    scorer = Scorer(FakeLLMClient(response_for(dimensions)))
    return await scorer.score(make_job_posting(), make_knowledge_base())


async def expect_scorer_error(dimensions):
    scorer = Scorer(FakeLLMClient(response_for(dimensions)))
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    return str(exc_info.value)


# --- Redistribution ---

@pytest.mark.asyncio
async def test_null_ai_redistributes_weight_to_hand_calculated_literal():
    """
    Applicable weights: 0.30 + 0.25 + 0.15 + 0.15 = 0.85

    0.30*80 = 24.0
    0.25*75 = 18.75
    0.15*80 = 12.0
    0.15*70 = 10.5
             ------
              65.25 / 0.85 = 76.76 -> 77
    """
    res = await score_with(dims(80, 75, None, 80, 70))
    assert res.score == 77


@pytest.mark.asyncio
async def test_same_dimensions_with_ai_at_zero_score_as_before():
    """
    0.30*80 = 24.0
    0.25*75 = 18.75
    0.15*0  =  0.0
    0.15*80 = 12.0
    0.15*70 = 10.5
             ------
              65.25 -> 65
    """
    res = await score_with(dims(80, 75, 0, 80, 70))
    assert res.score == 65


@pytest.mark.asyncio
async def test_no_nulls_gives_the_same_score_as_before():
    """
    0.30*80 = 24.0
    0.25*60 = 15.0
    0.15*40 =  6.0
    0.15*60 =  9.0
    0.15*40 =  6.0
             ------
              60.0
    """
    res = await score_with(dims(80, 60, 40, 60, 40))
    assert res.score == 60


@pytest.mark.asyncio
async def test_no_nulls_half_tie_still_rounds_half_to_even():
    """
    0.30*60 = 18.0
    0.25*40 = 10.0
    0.15*20 =  3.0
    0.15*50 =  7.5
    0.15*80 = 12.0
             ------
              50.5 -> 50 (round half to even, unchanged)
    """
    res = await score_with(dims(60, 40, 20, 50, 80))
    assert res.score == 50


@pytest.mark.asyncio
async def test_null_ai_with_every_other_dimension_100_scores_100():
    res = await score_with(dims(100, 100, None, 100, 100))
    assert res.score == 100


@pytest.mark.asyncio
async def test_null_ai_with_every_other_dimension_0_scores_0():
    res = await score_with(dims(0, 0, None, 0, 0))
    assert res.score == 0


def test_redistributed_score_stays_within_0_and_100():
    for values in itertools.product([0, 50, 100], repeat=4):
        for ai in [None, 0, 50, 100]:
            technical, requirements, seniority, domain = values
            score = compute_score(dims(technical, requirements, ai, seniority, domain))
            assert isinstance(score, int)
            assert 0 <= score <= 100


# --- Which dimensions may be null ---

@pytest.mark.asyncio
@pytest.mark.parametrize("key", NON_NULLABLE_KEYS)
async def test_null_on_a_non_nullable_dimension_raises_naming_it(key):
    dimensions = dims(80, 75, 60, 80, 70)
    dimensions[key] = None
    msg = await expect_scorer_error(dimensions)
    assert key in msg
    assert "null" in msg


@pytest.mark.asyncio
async def test_all_five_null_raises():
    msg = await expect_scorer_error(dims(None, None, None, None, None))
    assert "null" in msg


def test_compute_score_raises_when_no_dimension_applies():
    with pytest.raises(ScorerError) as exc_info:
        compute_score(dims(None, None, None, None, None))
    assert "No applicable dimensions" in str(exc_info.value)


@pytest.mark.asyncio
async def test_string_null_is_rejected_as_non_numeric():
    msg = await expect_scorer_error(dims(80, 75, "null", 80, 70))
    assert AI in msg
    assert "must be numeric" in msg


def test_system_prompt_schema_permits_null():
    assert "or null only where the evaluation criteria allow it" in SYSTEM_PROMPT


# --- Storage and display ---

@pytest.mark.asyncio
async def test_null_dimension_is_stored_as_none_not_zero():
    res = await score_with(dims(80, 75, None, 80, 70))
    assert res.dimension_breakdown[AI] is None
    assert res.dimension_breakdown["Technical role content"] == 80.0


@pytest.mark.asyncio
async def test_null_dimension_survives_persistence_round_trip(db_path):
    persistence = PersistenceService(db_path)
    job = make_job_posting(status=JobStatus.MATCHED)
    await persistence.upsert_job(job)
    match = MatchResult(
        identity_hash=job.identity_hash,
        score=77,
        dimension_breakdown={**dims(80.0, 75.0, None, 80.0, 70.0)},
        match_reasons=["AI literacy & development: not applicable."],
        scored_at=datetime.now(timezone.utc),
    )
    await persistence.save_match_result(match)

    conn = sqlite3.connect(db_path)
    try:
        raw = conn.execute(
            "SELECT dimensions FROM match_results WHERE identity_hash = ?;",
            (job.identity_hash,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert json.loads(raw)[AI] is None

    fetched = await persistence.get_match_result(job.identity_hash)
    assert fetched.dimension_breakdown[AI] is None

    listed = await persistence.list_jobs_with_match()
    assert len(listed) == 1
    assert listed[0].match.dimension_breakdown[AI] is None


def test_format_dimension_value_renders_null_as_not_applicable():
    assert format_dimension_value(None) == "n/a"
    assert format_dimension_value(0.0) == "0.0"
    assert format_dimension_value(80.0) == "80.0"


def test_view_state_passes_null_dimension_to_the_card():
    job = make_job_posting(status=JobStatus.MATCHED)
    match = MatchResult(
        identity_hash=job.identity_hash,
        score=77,
        dimension_breakdown=dims(80.0, 75.0, None, 80.0, 70.0),
        match_reasons=["reason"],
        scored_at=datetime.now(timezone.utc),
    )
    state = build_view_state(
        None,
        [JobWithMatch(job=job, match=match)],
        Counters(total=1, rejected=0, written=0, applied=0),
        {},
    )
    assert len(state.matches) == 1
    assert state.matches[0].dimension_breakdown[AI] is None
