"""
The two request shapes the OpenAI adapter sends.

Established against the live API on 2026-10-01 with a reasoning model:
  max_tokens                       -> 400 "Use 'max_completion_tokens' instead"
  temperature=0.0                  -> 400 "Only the default (1) value is supported"
  max_completion_tokens alone      -> 200
So the reasoning shape omits temperature entirely rather than sending 1, and the
ordinary shape must stay exactly as it was for Gemini and for OpenAI chat models.

The SDK client is replaced by a fake that records kwargs; no network call is made.
"""
import pytest

from llm.openai_adapter import REASONING_TOKEN_FLOOR, OpenAIAdapter
from llm.types import LLMProfile, LLMRequest


class FakeCompletions:
    def __init__(self, outer):
        self.outer = outer

    async def create(self, **kwargs):
        self.outer.kwargs = kwargs
        return self.outer.response


class FakeChat:
    def __init__(self, outer):
        self.completions = FakeCompletions(outer)


class FakeDetails:
    def __init__(self, reasoning_tokens):
        self.reasoning_tokens = reasoning_tokens


class FakeUsage:
    def __init__(self, reasoning_tokens=None, with_details=True):
        self.prompt_tokens = 11
        self.completion_tokens = 22
        if with_details:
            self.completion_tokens_details = FakeDetails(reasoning_tokens)


class FakeMessage:
    content = "answer text"


class FakeChoice:
    message = FakeMessage()


class FakeResponse:
    def __init__(self, usage):
        self.choices = [FakeChoice()]
        self.usage = usage


class FakeClient:
    """Stands in for openai.AsyncOpenAI, recording the kwargs it is called with."""

    def __init__(self, usage=None):
        self.kwargs = None
        self.response = FakeResponse(usage if usage is not None else FakeUsage())
        self.chat = FakeChat(self)


def profile(**overrides) -> LLMProfile:
    values = dict(
        provider="openai",
        model="a-model",
        api_key="adapter-test-key",
        input_token_rate_usd=0.0,
        output_token_rate_usd=0.0,
    )
    values.update(overrides)
    return LLMProfile(**values)


def request(**overrides) -> LLMRequest:
    values = dict(
        system_prompt="sys",
        user_prompt="usr",
        max_tokens=1000,
        temperature=0.0,
        json_mode=False,
    )
    values.update(overrides)
    return LLMRequest(**values)


async def call(adapter_profile, llm_request, usage=None):
    adapter = OpenAIAdapter(adapter_profile)
    client = FakeClient(usage)
    adapter.client = client
    response = await adapter._call(llm_request)
    return client.kwargs, response


# --- The reasoning shape ---

@pytest.mark.asyncio
async def test_reasoning_request_sends_max_completion_tokens_and_effort():
    kwargs, _ = await call(profile(reasoning=True), request())

    assert kwargs["max_completion_tokens"] == REASONING_TOKEN_FLOOR
    assert kwargs["reasoning_effort"] == "medium"
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs


@pytest.mark.asyncio
async def test_reasoning_floor_respects_a_larger_request():
    kwargs, _ = await call(profile(reasoning=True), request(max_tokens=12000))
    assert kwargs["max_completion_tokens"] == 12000


@pytest.mark.asyncio
async def test_request_effort_overrides_the_profile():
    kwargs, _ = await call(
        profile(reasoning=True, reasoning_effort="high"),
        request(reasoning_effort="low"),
    )
    assert kwargs["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_profile_effort_is_used_when_the_request_asks_for_nothing():
    kwargs, _ = await call(profile(reasoning=True, reasoning_effort="high"), request())
    assert kwargs["reasoning_effort"] == "high"


# --- The ordinary shape ---

@pytest.mark.asyncio
async def test_non_reasoning_request_is_unchanged():
    kwargs, _ = await call(profile(), request(temperature=0.7))

    assert kwargs["max_tokens"] == 1000
    assert kwargs["temperature"] == 0.7
    assert "max_completion_tokens" not in kwargs
    assert "reasoning_effort" not in kwargs


@pytest.mark.asyncio
async def test_a_request_effort_is_ignored_without_a_reasoning_profile():
    kwargs, _ = await call(profile(), request(reasoning_effort="low"))
    assert "reasoning_effort" not in kwargs
    assert kwargs["temperature"] == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("reasoning", [True, False])
async def test_json_mode_is_independent_of_reasoning(reasoning):
    kwargs, _ = await call(profile(reasoning=reasoning), request(json_mode=True))
    assert kwargs["response_format"] == {"type": "json_object"}


# --- Reasoning tokens ---

@pytest.mark.asyncio
async def test_reasoning_tokens_are_reported_when_present():
    _, response = await call(profile(reasoning=True), request(), usage=FakeUsage(reasoning_tokens=140))
    assert response.usage.reasoning_tokens == 140
    # Billing reads output_tokens, which already includes them.
    assert response.usage.output_tokens == 22


@pytest.mark.asyncio
async def test_reasoning_tokens_are_none_when_absent():
    _, response = await call(profile(), request(), usage=FakeUsage(with_details=False))
    assert response.usage.reasoning_tokens is None


@pytest.mark.asyncio
async def test_missing_usage_does_not_raise():
    _, response = await call(profile(), request(), usage=False or None)
    assert response.usage.input_tokens == 11


# --- What each skill asks for ---

def test_scorer_asks_for_low_effort():
    """Five values off a rubric need no deliberation, and effort is billed as output."""
    import inspect

    from skills import scorer

    source = inspect.getsource(scorer.Scorer.score)
    assert 'reasoning_effort="low"' in source


def test_writer_asks_for_no_effort_and_takes_the_profile_default():
    import inspect

    from skills import writer

    source = inspect.getsource(writer.Writer.generate)
    assert "reasoning_effort" not in source
