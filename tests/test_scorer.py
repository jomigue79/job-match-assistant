import json
import pytest
from datetime import datetime, timezone

from domain import JobPosting, JobStatus
from knowledge import KnowledgeBase
from llm import LLMRequest, LLMResponse, LLMUsage
from observability import CostAccumulator
from skills.scorer import Scorer, ScorerError

# The five dimension keys Scorer requires, with values chosen once so tests that
# are really about something else (fences, reasons, prompt shape, cost) can reach
# their assertion instead of tripping the dimension-key check.
#
# Weighted sum: 0.30*60 + 0.25*40 + 0.15*20 + 0.15*50 + 0.15*80
#             =   18.0 +   10.0 +    3.0 +    7.5 +   12.0 = 50.5
# round(50.5) is 50, not 51 -- Python rounds halves to even.
VALID_DIMENSIONS = {
    "Technical role content": 60,
    "Requirements coverage": 40,
    "AI literacy & development": 20,
    "Seniority & scope": 50,
    "Domain & context": 80,
}
VALID_DIMENSIONS_SCORE = 50

class FakeLLMClient:
    def __init__(self, response_text: str, input_tokens: int = 10, output_tokens: int = 20):
        self.response_text = response_text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.last_request = None
        self.last_cost_accumulator = None

    async def complete(self, request: LLMRequest, cost_accumulator = None) -> LLMResponse:
        self.last_request = request
        self.last_cost_accumulator = cost_accumulator
        if cost_accumulator is not None:
            cost_accumulator.add_llm_usage(self.input_tokens, self.output_tokens, calls=1)
        return LLMResponse(
            text=self.response_text,
            usage=LLMUsage(input_tokens=self.input_tokens, output_tokens=self.output_tokens),
            model="fake-model"
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
        status=JobStatus.SCRAPED
    )

def make_knowledge_base():
    return KnowledgeBase(
        cv="My CV details: Python expert.",
        persona="Write in a friendly tone.",
        ats_criteria="Look for Python experience."
    )

@pytest.mark.asyncio
async def test_valid_response():
    valid_json = json.dumps({
        "dimensions": VALID_DIMENSIONS,
        "reasons": ["Has solid Python skills", "Good testing experience"],
    })
    fake_client = FakeLLMClient(valid_json)
    scorer = Scorer(fake_client)

    job = make_job_posting()
    kb = make_knowledge_base()

    res = await scorer.score(job, kb)

    assert res.identity_hash == job.identity_hash
    # The model returned no score; Scorer computed this from the dimensions.
    assert res.score == VALID_DIMENSIONS_SCORE
    assert res.dimension_breakdown == {k: float(v) for k, v in VALID_DIMENSIONS.items()}
    assert res.match_reasons == ["Has solid Python skills", "Good testing experience"]
    assert res.scored_at is not None
    assert res.scored_at.tzinfo == timezone.utc

@pytest.mark.asyncio
async def test_markdown_fenced_json():
    inner = json.dumps({"dimensions": VALID_DIMENSIONS, "reasons": ["Excellent coder"]})
    fenced_json = f'```json\n{inner}\n```'
    fake_client = FakeLLMClient(fenced_json)
    scorer = Scorer(fake_client)

    job = make_job_posting()
    kb = make_knowledge_base()

    res = await scorer.score(job, kb)
    assert res.score == VALID_DIMENSIONS_SCORE
    assert res.dimension_breakdown == {k: float(v) for k, v in VALID_DIMENSIONS.items()}

@pytest.mark.asyncio
async def test_malformed_non_json():
    fake_client = FakeLLMClient("not a json")
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "not valid JSON" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_missing_dimensions():
    fake_client = FakeLLMClient('{"score": 80, "reasons": ["Y"]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "missing 'dimensions'" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_empty_dimensions():
    fake_client = FakeLLMClient('{"score": 80, "dimensions": {}, "reasons": ["Y"]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "dimensions' field must be a non-empty dictionary" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_dimensions_non_numeric():
    # Correct key set, one bad value -- otherwise the key check fires first.
    dims = dict(VALID_DIMENSIONS)
    dims["Domain & context"] = "invalid"
    fake_client = FakeLLMClient(json.dumps({"dimensions": dims, "reasons": ["Y"]}))
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "must be numeric" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_dimensions_boolean():
    dims = dict(VALID_DIMENSIONS)
    dims["Domain & context"] = True
    fake_client = FakeLLMClient(json.dumps({"dimensions": dims, "reasons": ["Y"]}))
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "must be numeric" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_missing_reasons():
    fake_client = FakeLLMClient(json.dumps({"dimensions": VALID_DIMENSIONS}))
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "missing 'reasons'" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_empty_reasons():
    fake_client = FakeLLMClient(json.dumps({"dimensions": VALID_DIMENSIONS, "reasons": []}))
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "reasons' field must be a non-empty list" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_reasons_non_string():
    fake_client = FakeLLMClient(json.dumps({"dimensions": VALID_DIMENSIONS, "reasons": [123]}))
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "elements must be non-empty strings" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_reasons_empty_string():
    fake_client = FakeLLMClient(json.dumps({"dimensions": VALID_DIMENSIONS, "reasons": ["   "]}))
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "elements must be non-empty strings" in str(exc_info.value)

@pytest.mark.asyncio
async def test_prompt_content_and_request_shape():
    valid_json = json.dumps({"dimensions": VALID_DIMENSIONS, "reasons": ["Y"]})
    fake_client = FakeLLMClient(valid_json)
    scorer = Scorer(fake_client)

    job = make_job_posting()
    kb = make_knowledge_base()

    await scorer.score(job, kb)

    req = fake_client.last_request
    assert req is not None

    assert kb.cv in req.user_prompt
    assert kb.ats_criteria in req.user_prompt

    assert kb.persona not in req.user_prompt
    assert kb.persona not in req.system_prompt

    assert req.temperature == 0.0
    assert req.json_mode is True
    assert req.max_tokens == 1000

@pytest.mark.asyncio
async def test_cost_accumulator_forwarding():
    valid_json = json.dumps({"dimensions": VALID_DIMENSIONS, "reasons": ["Y"]})
    fake_client = FakeLLMClient(valid_json, input_tokens=15, output_tokens=25)
    scorer = Scorer(fake_client)

    cost_acc = CostAccumulator(
        llm_input_token_rate_usd=0.01,
        llm_output_token_rate_usd=0.02
    )

    job = make_job_posting()
    kb = make_knowledge_base()

    await scorer.score(job, kb, cost_accumulator=cost_acc)

    assert fake_client.last_cost_accumulator is cost_acc

    summary = cost_acc.summary()
    assert summary.total_input_tokens == 15
    assert summary.total_output_tokens == 25
    assert summary.total_llm_calls == 1
    assert summary.estimated_cost_usd == 0.65
