import pytest
from datetime import datetime, timezone

from domain import JobPosting, JobStatus, MatchResult
from knowledge import KnowledgeBase
from llm import LLMRequest, LLMResponse, LLMUsage
from observability import CostAccumulator
from skills.writer import Writer, WriterError

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
        cv="My CV details: Python expert with 10 years experience.",
        persona="Write in a friendly tone and sardonic style.",
        ats_criteria="Look for Python experience."
    )

def make_match_result():
    return MatchResult(
        identity_hash="fake-hash",
        score=95,
        dimension_breakdown={"overall": 9.5},
        match_reasons=["Has extensive Python experience", "Proven track record"],
        scored_at=datetime.now(timezone.utc)
    )

# A sample 120 character string to pass the length check
SAMPLE_LONG_RESPONSE = "Dear Hiring Manager, I am writing to express my strong interest in the Software Engineer position. With over ten years of dedicated Python engineering, I possess the required qualifications. I look forward to contributing."

@pytest.mark.asyncio
async def test_writer_happy_path():
    fake_client = FakeLLMClient(SAMPLE_LONG_RESPONSE)
    writer = Writer(fake_client)

    job = make_job_posting()
    kb = make_knowledge_base()
    mr = make_match_result()

    letter = await writer.generate(job, kb, mr)
    assert letter == SAMPLE_LONG_RESPONSE
    assert len(letter) >= 100

@pytest.mark.asyncio
async def test_writer_prompt_content_and_request_shape():
    fake_client = FakeLLMClient(SAMPLE_LONG_RESPONSE)
    writer = Writer(fake_client)

    job = make_job_posting()
    kb = make_knowledge_base()
    mr = make_match_result()

    await writer.generate(job, kb, mr)

    req = fake_client.last_request
    assert req is not None

    # Assert CV and Persona are in the user prompt
    assert kb.cv in req.user_prompt
    assert kb.persona in req.user_prompt

    # Assert ats_criteria is NOT in the prompts (R-3 Boundary)
    assert kb.ats_criteria not in req.user_prompt
    assert kb.ats_criteria not in req.system_prompt

    # Assert all match reasons are present in the user prompt
    for reason in mr.match_reasons:
        assert reason in req.user_prompt

    # Assert anti-fabrication instruction is in the system prompt
    assert "ANTI-FABRICATION RULE" in req.system_prompt
    assert "ONLY assert qualifications" in req.system_prompt

    # Assert request shape
    assert req.temperature == 0.7
    assert req.json_mode is False
    assert req.max_tokens == 1500

@pytest.mark.asyncio
async def test_writer_empty_response():
    fake_client = FakeLLMClient("")
    writer = Writer(fake_client)

    job = make_job_posting()
    kb = make_knowledge_base()
    mr = make_match_result()

    with pytest.raises(WriterError) as exc_info:
        await writer.generate(job, kb, mr)
    assert "Cover letter generation failed" in str(exc_info.value)

@pytest.mark.asyncio
async def test_writer_too_short_response():
    fake_client = FakeLLMClient("Too short response text.")
    writer = Writer(fake_client)

    job = make_job_posting()
    kb = make_knowledge_base()
    mr = make_match_result()

    with pytest.raises(WriterError) as exc_info:
        await writer.generate(job, kb, mr)
    assert "too short" in str(exc_info.value)

@pytest.mark.asyncio
async def test_writer_cost_accumulator_forwarding():
    fake_client = FakeLLMClient(SAMPLE_LONG_RESPONSE, input_tokens=50, output_tokens=150)
    writer = Writer(fake_client)

    cost_acc = CostAccumulator(
        llm_input_token_rate_usd=0.01,
        llm_output_token_rate_usd=0.02
    )

    job = make_job_posting()
    kb = make_knowledge_base()
    mr = make_match_result()

    await writer.generate(job, kb, mr, cost_accumulator=cost_acc)

    assert fake_client.last_cost_accumulator is cost_acc

    summary = cost_acc.summary()
    assert summary.total_input_tokens == 50
    assert summary.total_output_tokens == 150
    assert summary.total_llm_calls == 1
    assert summary.estimated_cost_usd == 3.5
