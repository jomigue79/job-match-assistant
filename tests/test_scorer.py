import pytest
from datetime import datetime, timezone

from domain import JobPosting, JobStatus
from knowledge import KnowledgeBase
from llm import LLMRequest, LLMResponse, LLMUsage
from observability import CostAccumulator
from skills.scorer import Scorer, ScorerError

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
    valid_json = '{"score": 85, "dimensions": {"Python": 9.0, "Testing": 8.0}, "reasons": ["Has solid Python skills", "Good testing experience"]}'
    fake_client = FakeLLMClient(valid_json)
    scorer = Scorer(fake_client)
    
    job = make_job_posting()
    kb = make_knowledge_base()
    
    res = await scorer.score(job, kb)
    
    assert res.identity_hash == job.identity_hash
    assert res.score == 85
    assert res.dimension_breakdown == {"Python": 9.0, "Testing": 8.0}
    assert res.match_reasons == ["Has solid Python skills", "Good testing experience"]
    assert res.scored_at is not None
    assert res.scored_at.tzinfo == timezone.utc

@pytest.mark.asyncio
async def test_markdown_fenced_json():
    fenced_json = '```json\n{"score": 90, "dimensions": {"Coding": 95}, "reasons": ["Excellent coder"]}\n```'
    fake_client = FakeLLMClient(fenced_json)
    scorer = Scorer(fake_client)
    
    job = make_job_posting()
    kb = make_knowledge_base()
    
    res = await scorer.score(job, kb)
    assert res.score == 90
    assert res.dimension_breakdown == {"Coding": 95.0}

@pytest.mark.asyncio
async def test_malformed_non_json():
    fake_client = FakeLLMClient("not a json")
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "not valid JSON" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_missing_score():
    fake_client = FakeLLMClient('{"dimensions": {"X": 1}, "reasons": ["Y"]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "missing 'score'" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_invalid_score_type():
    fake_client = FakeLLMClient('{"score": "eighty", "dimensions": {"X": 1}, "reasons": ["Y"]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "score' field must be an integer" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_boolean_score():
    fake_client = FakeLLMClient('{"score": true, "dimensions": {"X": 1}, "reasons": ["Y"]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "score' field must be an integer" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_score_out_of_bounds_high():
    fake_client = FakeLLMClient('{"score": 101, "dimensions": {"X": 1}, "reasons": ["Y"]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "must be in range" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_score_out_of_bounds_low():
    fake_client = FakeLLMClient('{"score": -1, "dimensions": {"X": 1}, "reasons": ["Y"]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "must be in range" in str(exc_info.value)

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
    fake_client = FakeLLMClient('{"score": 80, "dimensions": {"X": "invalid"}, "reasons": ["Y"]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "must be numeric" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_dimensions_boolean():
    fake_client = FakeLLMClient('{"score": 80, "dimensions": {"X": true}, "reasons": ["Y"]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "must be numeric" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_missing_reasons():
    fake_client = FakeLLMClient('{"score": 80, "dimensions": {"X": 1}}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "missing 'reasons'" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_empty_reasons():
    fake_client = FakeLLMClient('{"score": 80, "dimensions": {"X": 1}, "reasons": []}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "reasons' field must be a non-empty list" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_reasons_non_string():
    fake_client = FakeLLMClient('{"score": 80, "dimensions": {"X": 1}, "reasons": [123]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "elements must be non-empty strings" in str(exc_info.value)

@pytest.mark.asyncio
async def test_malformed_reasons_empty_string():
    fake_client = FakeLLMClient('{"score": 80, "dimensions": {"X": 1}, "reasons": ["   "]}')
    scorer = Scorer(fake_client)
    with pytest.raises(ScorerError) as exc_info:
        await scorer.score(make_job_posting(), make_knowledge_base())
    assert "elements must be non-empty strings" in str(exc_info.value)

@pytest.mark.asyncio
async def test_prompt_content_and_request_shape():
    valid_json = '{"score": 90, "dimensions": {"X": 1}, "reasons": ["Y"]}'
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
    valid_json = '{"score": 90, "dimensions": {"X": 1}, "reasons": ["Y"]}'
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
