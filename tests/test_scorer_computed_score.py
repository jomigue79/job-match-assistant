"""
Scorer computes the score from the model's dimensions; the model does not
return one.

On run a7640ad9 (2026-09-08) the model was asked for the weighted sum and got it
wrong on 8 of 10 sampled rows, by up to 16 points in both directions. The
arithmetic now lives in code, so these tests pin it.

Expected scores below are hand-calculated and written as literals. They are
deliberately NOT recomputed from DIMENSION_WEIGHTS -- a test that derives its
expectation from the code under test would pass even if the weights were wrong.

Hand-rolled fakes only; no mocking library.
"""
import json
import pytest
from datetime import datetime, timezone

from domain import JobPosting, JobStatus
from knowledge import KnowledgeBase
from llm import LLMRequest, LLMResponse, LLMUsage
from skills.scorer import Scorer, ScorerError

DIMENSION_KEYS = [
    "Technical role content",
    "Requirements coverage",
    "AI literacy & development",
    "Seniority & scope",
    "Domain & context",
]


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


def make_job_posting():
    return JobPosting(
        company="Test Company",
        title="Software Engineer",
        location="Remote",
        url="http://example.com",
        description="Write Python code and test it.",
        source="test_source",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.SCRAPED,
    )


def make_knowledge_base():
    return KnowledgeBase(
        cv="My CV details: Python expert.",
        persona="Write in a friendly tone.",
        ats_criteria="Look for Python experience.",
    )


def dims(technical, requirements, ai, seniority, domain):
    return dict(zip(DIMENSION_KEYS, [technical, requirements, ai, seniority, domain]))


def response_for(dimensions, reasons=None):
    return json.dumps({
        "dimensions": dimensions,
        "reasons": reasons or ["A reason for each dimension."],
    })


async def score_with(dimensions):
    scorer = Scorer(FakeLLMClient(response_for(dimensions)))
    return await scorer.score(make_job_posting(), make_knowledge_base())


async def expect_scorer_error(dimensions):
    scorer = Scorer(FakeLLMClient(response_for(dimensions)))
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    return str(exc_info.value)


# --- Correct computation ---

@pytest.mark.asyncio
async def test_computes_score_from_known_dimensions():
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
async def test_computes_score_second_known_case():
    """
    0.30*70 = 21.0
    0.25*20 =  5.0
    0.15* 0 =  0.0
    0.15*50 =  7.5
    0.15*70 = 10.5
             ------
              44.0
    """
    res = await score_with(dims(70, 20, 0, 50, 70))
    assert res.score == 44


@pytest.mark.asyncio
async def test_score_is_not_the_model_holistic_guess():
    """
    A score key in the model's output is ignored entirely -- this is the defect
    the change exists to remove. Dimensions here compute to 60; the model's
    claimed 12 must not survive.
    """
    payload = json.dumps({
        "score": 12,
        "dimensions": dims(80, 60, 40, 60, 40),
        "reasons": ["A reason."],
    })
    scorer = Scorer(FakeLLMClient(payload))
    res = await scorer.score(make_job_posting(), make_knowledge_base())
    assert res.score == 60


# --- Boundaries ---

@pytest.mark.asyncio
async def test_all_zero_dimensions_compute_to_zero():
    """This is the gated-posting case: every dimension 0, so the score is 0."""
    res = await score_with(dims(0, 0, 0, 0, 0))
    assert res.score == 0
    assert res.dimension_breakdown == {k: 0.0 for k in DIMENSION_KEYS}


@pytest.mark.asyncio
async def test_all_max_dimensions_compute_to_one_hundred():
    """Upper bound: weights sum to 1.0, so all-100 gives exactly 100."""
    res = await score_with(dims(100, 100, 100, 100, 100))
    assert res.score == 100


# --- Float values and rounding ---

@pytest.mark.asyncio
async def test_float_dimension_values_round_correctly():
    """
    0.30*55.5 = 16.65
    0.25*44.4 = 11.10
    0.15*33.3 =  4.995
    0.15*22.2 =  3.33
    0.15*11.1 =  1.665
                ------
                37.74  -> 38
    """
    res = await score_with(dims(55.5, 44.4, 33.3, 22.2, 11.1))
    assert res.score == 38


@pytest.mark.asyncio
async def test_float_dimension_values_round_down():
    """
    0.30*70.5 = 21.15
    0.25*20.4 =  5.10
    0.15* 0   =  0.0
    0.15*50.2 =  7.53
    0.15*70.9 = 10.635
                ------
                44.415 -> 44
    """
    res = await score_with(dims(70.5, 20.4, 0, 50.2, 70.9))
    assert res.score == 44


@pytest.mark.asyncio
async def test_exact_half_rounds_to_even():
    """
    0.30*60 = 18.0
    0.25*40 = 10.0
    0.15*20 =  3.0
    0.15*50 =  7.5
    0.15*80 = 12.0
             ------
              50.5 -> 50, because Python rounds halves to even, not up.
    """
    res = await score_with(dims(60, 40, 20, 50, 80))
    assert res.score == 50


# --- Dimension-set validation ---

@pytest.mark.asyncio
async def test_missing_dimension_key_raises():
    incomplete = dims(80, 60, 40, 60, 40)
    del incomplete["Domain & context"]
    message = await expect_scorer_error(incomplete)
    assert "missing expected key(s)" in message
    assert "Domain & context" in message


@pytest.mark.asyncio
async def test_extra_dimension_key_raises():
    padded = dims(80, 60, 40, 60, 40)
    padded["Culture fit"] = 50
    message = await expect_scorer_error(padded)
    assert "unexpected key(s)" in message
    assert "Culture fit" in message


@pytest.mark.asyncio
async def test_renamed_dimension_key_raises_both_ways():
    """A renamed key is simultaneously a missing key and an extra one."""
    renamed = dims(80, 60, 40, 60, 40)
    renamed["Technical content"] = renamed.pop("Technical role content")
    message = await expect_scorer_error(renamed)
    assert "missing expected key(s)" in message
    assert "Technical role content" in message


# --- Value-range validation ---

@pytest.mark.asyncio
async def test_dimension_value_above_one_hundred_raises():
    message = await expect_scorer_error(dims(101, 60, 40, 60, 40))
    assert "must be in range [0, 100]" in message
    assert "Technical role content" in message


@pytest.mark.asyncio
async def test_dimension_value_below_zero_raises():
    message = await expect_scorer_error(dims(80, 60, 40, 60, -1))
    assert "must be in range [0, 100]" in message
    assert "Domain & context" in message


@pytest.mark.asyncio
async def test_dimension_value_far_out_of_range_raises():
    message = await expect_scorer_error(dims(80, 60, 1000, 60, 40))
    assert "must be in range [0, 100]" in message
    assert "AI literacy & development" in message
