"""
The writer gets its own LLM profile.

The scorer runs on every scraped job and must stay on a cheap model; the writer runs
once per letter. One LLM_* triple used to serve both, because build_llm_client() took
no arguments and both adapters read settings in argument-less constructors. An
LLMProfile now carries provider, model, key, endpoint and rates, and the writer is
built from its own.

Every unset WRITER_LLM_* field falls back to its LLM_* counterpart, so a tree with no
writer settings behaves exactly as before.

Hand-rolled fakes only; no mocking library. Nothing here makes a network call: both
SDK clients construct offline, and no real adapter's _call is ever reached.
"""
from datetime import datetime, timezone

import pytest

from config import get_settings, ConfigurationError
from domain import JobPosting, JobStatus, MatchResult
from knowledge import KnowledgeBase
from llm.client import LLMClient, build_llm_client
from llm.openai_adapter import OpenAIAdapter
from llm.types import LLMProfile, LLMRequest, LLMResponse, LLMUsage
from skills.scorer import Scorer
from skills.writer import Writer

MAIN_KEY = "main-key-not-a-real-credential"
WRITER_KEY = "writer-key-not-a-real-credential"

LETTER = (
    "Hello Test Company team,\n\n"
    "A letter body long enough to clear the writer's hundred-character minimum "
    "without actually saying anything about anyone.\n\n"
    "Kind regards,\nTest Name"
)


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    """A complete main profile, and no writer settings unless a test adds them."""
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "main-model")
    monkeypatch.setenv("LLM_API_KEY", MAIN_KEY)
    monkeypatch.setenv("LLM_INPUT_TOKEN_RATE_USD", "0.000001")
    monkeypatch.setenv("LLM_OUTPUT_TOKEN_RATE_USD", "0.000002")
    for name in [
        "LLM_BASE_URL",
        "WRITER_LLM_PROVIDER",
        "WRITER_LLM_MODEL",
        "WRITER_LLM_API_KEY",
        "WRITER_LLM_BASE_URL",
        "WRITER_LLM_INPUT_TOKEN_RATE_USD",
        "WRITER_LLM_OUTPUT_TOKEN_RATE_USD",
        "WRITER_LLM_REASONING",
        "WRITER_LLM_REASONING_EFFORT",
    ]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def settings_now():
    get_settings.cache_clear()
    return get_settings()


def profile(**overrides) -> LLMProfile:
    values = dict(
        provider="openai",
        model="m-test",
        api_key="profile-test-key",
        base_url=None,
        input_token_rate_usd=0.0,
        output_token_rate_usd=0.0,
    )
    values.update(overrides)
    return LLMProfile(**values)


class FakeAdapter:
    """Carries a profile the way a real adapter does, with no SDK behind it."""
    provider_name = "fake-provider"

    def __init__(self, llm_profile: LLMProfile):
        self.profile = llm_profile
        self.model = llm_profile.model

    async def _call(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text=LETTER,
            usage=LLMUsage(input_tokens=100, output_tokens=200),
            model=self.model,
        )


class RecordingClient:
    """An LLM client that records the accumulator the writer hands it."""

    def __init__(self, llm_profile: LLMProfile):
        self.adapter = FakeAdapter(llm_profile)
        self.last_cost_accumulator = None

    async def complete(self, request: LLMRequest, cost_accumulator=None) -> LLMResponse:
        self.last_cost_accumulator = cost_accumulator
        if cost_accumulator is not None:
            cost_accumulator.add_llm_usage(input_tokens=100, output_tokens=200, calls=1)
        return await self.adapter._call(request)


def make_job():
    return JobPosting(
        company="Test Company",
        title="Project Manager",
        location="Porto",
        url="http://example.com",
        description="Lead delivery of a product team.",
        source="test_source",
        scraped_at=datetime.now(timezone.utc),
        status=JobStatus.MATCHED,
    )


def make_knowledge():
    return KnowledgeBase(cv="CV text.", persona="Write plainly.", ats_criteria="Criteria.")


def make_match():
    return MatchResult(
        identity_hash="fake-hash",
        score=70,
        dimension_breakdown={"Technical role content": 80.0},
        match_reasons=["Owns software delivery"],
        scored_at=datetime.now(timezone.utc),
    )


# --- Fallback ---

def test_writer_profile_falls_back_field_by_field():
    settings = settings_now()
    assert settings.writer_llm_profile() == settings.main_llm_profile()


def test_writer_profile_overrides_only_what_is_set(monkeypatch):
    monkeypatch.setenv("WRITER_LLM_MODEL", "writer-model")
    settings = settings_now()
    main, writer = settings.main_llm_profile(), settings.writer_llm_profile()

    assert writer.model == "writer-model"
    assert main.model == "main-model"
    assert writer.provider == main.provider
    assert writer.api_key.get_secret_value() == main.api_key.get_secret_value()
    assert writer.input_token_rate_usd == main.input_token_rate_usd
    assert writer.output_token_rate_usd == main.output_token_rate_usd


def test_writer_overriding_provider_does_not_inherit_base_url(monkeypatch):
    """The main endpoint belongs to the main provider's API; the writer's does not."""
    monkeypatch.setenv("LLM_BASE_URL", "https://compatible.example.com")
    monkeypatch.setenv("WRITER_LLM_PROVIDER", "google")
    monkeypatch.setenv("WRITER_LLM_API_KEY", WRITER_KEY)
    settings = settings_now()

    assert settings.main_llm_profile().base_url == "https://compatible.example.com"
    assert settings.writer_llm_profile().base_url is None


# --- Validation ---

def test_writer_provider_without_its_own_key_raises(monkeypatch):
    monkeypatch.setenv("WRITER_LLM_PROVIDER", "google")
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError) as exc_info:
        get_settings()
    message = str(exc_info.value)
    assert "WRITER_LLM_API_KEY is required" in message
    assert "'google'" in message
    assert "'openai'" in message


def test_writer_base_url_with_google_raises(monkeypatch):
    monkeypatch.setenv("WRITER_LLM_PROVIDER", "google")
    monkeypatch.setenv("WRITER_LLM_API_KEY", WRITER_KEY)
    monkeypatch.setenv("WRITER_LLM_BASE_URL", "https://example.com")
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError) as exc_info:
        get_settings()
    assert "WRITER_LLM_BASE_URL" in str(exc_info.value)
    assert "'google'" in str(exc_info.value)


def test_main_base_url_with_google_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com")
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError) as exc_info:
        get_settings()
    assert "LLM_BASE_URL" in str(exc_info.value)
    assert "'google'" in str(exc_info.value)


# --- Who gets which profile ---

def test_build_llm_client_uses_the_given_profile():
    assert build_llm_client(profile(model="m-test")).adapter.model == "m-test"
    assert build_llm_client().adapter.model == "main-model"


def test_adapter_reads_nothing_from_settings(monkeypatch):
    """The adapter used to read LLM_MODEL itself; the profile is now the only source."""
    monkeypatch.setenv("LLM_MODEL", "ignored-by-the-adapter")
    get_settings.cache_clear()

    adapter = OpenAIAdapter(profile(model="profile-model", base_url="https://compatible.example.com"))

    assert adapter.model == "profile-model"
    assert str(adapter.client.base_url).startswith("https://compatible.example.com")


def test_scorer_uses_the_main_profile(monkeypatch):
    monkeypatch.setenv("WRITER_LLM_MODEL", "writer-model")
    settings = settings_now()

    scorer = Scorer(build_llm_client())
    writer = Writer(build_llm_client(settings.writer_llm_profile()))

    assert scorer.llm_client.adapter.model == "main-model"
    assert writer.llm_client.adapter.model == "writer-model"


# --- Cost ---

@pytest.mark.asyncio
async def test_writer_cost_uses_writer_rates(monkeypatch):
    """
    A letter is generated outside a run, so no accumulator is handed in. The writer builds
    one from its own rates: 100 input and 200 output tokens at 0.001 and 0.002 is 0.5,
    where the main rates would have given 0.0005.
    """
    monkeypatch.setenv("WRITER_LLM_MODEL", "writer-model")
    monkeypatch.setenv("WRITER_LLM_INPUT_TOKEN_RATE_USD", "0.001")
    monkeypatch.setenv("WRITER_LLM_OUTPUT_TOKEN_RATE_USD", "0.002")
    settings = settings_now()

    client = RecordingClient(settings.writer_llm_profile())
    letter = await Writer(client).generate(make_job(), make_knowledge(), make_match())

    assert letter == LETTER
    accumulator = client.last_cost_accumulator
    assert accumulator is not None
    assert accumulator.llm_input_token_rate_usd == 0.001
    assert accumulator.llm_output_token_rate_usd == 0.002
    assert accumulator.summary().estimated_cost_usd == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_a_passed_accumulator_is_still_used(monkeypatch):
    """The run path hands one in; the writer must not replace it."""
    from observability import CostAccumulator

    settings = settings_now()
    client = RecordingClient(settings.writer_llm_profile())
    given = CostAccumulator(llm_input_token_rate_usd=0.01, llm_output_token_rate_usd=0.02)

    await Writer(client).generate(make_job(), make_knowledge(), make_match(), cost_accumulator=given)

    assert client.last_cost_accumulator is given


# --- Logging ---

@pytest.mark.asyncio
async def test_log_line_names_provider_and_model_and_not_the_key(monkeypatch):
    """A log line has to say which provider answered; it must never carry the key."""
    events = []

    class RecordingLogger:
        def info(self, event, **kw):
            events.append((event, kw))

        warning = info
        error = info

    # llm.client, not src.llm.client: the two import paths are different module
    # objects, and only this one is the module the client actually runs from.
    monkeypatch.setattr("llm.client.logger", RecordingLogger())

    client = LLMClient(
        FakeAdapter(profile(model="logged-model", api_key=MAIN_KEY)),
        concurrency=1,
        max_retries=1,
        timeout_seconds=5,
    )
    await client.complete(LLMRequest(system_prompt="sys", user_prompt="usr", max_tokens=10, temperature=0.0))

    assert events, "the client logged nothing"
    event, fields = events[0]
    assert event == "LLM complete success"
    assert fields["provider"] == "fake-provider"
    assert fields["model"] == "logged-model"
    assert MAIN_KEY not in repr(events)
