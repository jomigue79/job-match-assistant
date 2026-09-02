import asyncio
import pytest
import pathlib
from pydantic import SecretStr

from config import get_settings
from observability import CostAccumulator
from llm.types import (
    LLMRequest,
    LLMResponse,
    LLMUsage,
    TransientLLMError,
    PermanentLLMError,
)
from llm.client import LLMClient, build_llm_client
from llm.adapter import PROVIDER_REGISTRY, get_adapter

_real_sleep = asyncio.sleep

@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure that provider registry additions are isolated to individual tests."""
    original = dict(PROVIDER_REGISTRY)
    yield
    PROVIDER_REGISTRY.clear()
    PROVIDER_REGISTRY.update(original)

class SleepingFakeAdapter:
    provider_name = "sleeping_fake"
    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0
        self.lock = asyncio.Lock()

    async def _call(self, request: LLMRequest) -> LLMResponse:
        async with self.lock:
            self.in_flight += 1
            if self.in_flight > self.max_in_flight:
                self.max_in_flight = self.in_flight
        try:
            await asyncio.sleep(0.02)
            return LLMResponse(
                text="fake text",
                usage=LLMUsage(input_tokens=5, output_tokens=10),
                model="sleeping_fake_model"
            )
        finally:
            async with self.lock:
                self.in_flight -= 1

class SimpleFakeAdapter:
    provider_name = "simple_fake"
    async def _call(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text="hello",
            usage=LLMUsage(input_tokens=12, output_tokens=34),
            model="simple-model"
        )

class TransientFailureAdapter:
    provider_name = "transient_fake"
    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.attempts = 0

    async def _call(self, request: LLMRequest) -> LLMResponse:
        self.attempts += 1
        if self.attempts <= self.fail_count:
            raise TransientLLMError("Transient error")
        return LLMResponse(
            text="eventual success",
            usage=LLMUsage(input_tokens=5, output_tokens=5),
            model="retry-model"
        )

class PermanentFailureAdapter:
    provider_name = "permanent_fake"
    def __init__(self):
        self.attempts = 0

    async def _call(self, request: LLMRequest) -> LLMResponse:
        self.attempts += 1
        raise PermanentLLMError("Permanent error")

class HangingFakeAdapter:
    provider_name = "hanging_fake"
    def __init__(self):
        self.attempts = 0

    async def _call(self, request: LLMRequest) -> LLMResponse:
        self.attempts += 1
        await _real_sleep(1.0)
        return LLMResponse(
            text="should not reach here",
            usage=LLMUsage(input_tokens=0, output_tokens=0),
            model="hanging-model"
        )

@pytest.mark.asyncio
async def test_concurrency_cap():
    adapter = SleepingFakeAdapter()
    client = LLMClient(adapter, concurrency=5, max_retries=3, timeout_seconds=10)
    
    req = LLMRequest(system_prompt="sys", user_prompt="usr", max_tokens=10, temperature=0.5)
    tasks = [client.complete(req) for _ in range(20)]
    results = await asyncio.gather(*tasks)
    
    assert len(results) == 20
    assert adapter.max_in_flight <= 5
    assert all(r.text == "fake text" for r in results)

@pytest.mark.asyncio
async def test_usage_and_cost_accumulator():
    adapter = SimpleFakeAdapter()
    client = LLMClient(adapter, concurrency=2, max_retries=1, timeout_seconds=5)
    
    cost_acc = CostAccumulator(
        llm_input_token_rate_usd=0.001,
        llm_output_token_rate_usd=0.002
    )
    req = LLMRequest(system_prompt="sys", user_prompt="usr", max_tokens=10, temperature=0.5)
    response = await client.complete(req, cost_accumulator=cost_acc)
    
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 34
    
    summary = cost_acc.summary()
    assert summary.total_input_tokens == 12
    assert summary.total_output_tokens == 34
    assert summary.total_llm_calls == 1
    assert summary.estimated_cost_usd == 0.080

@pytest.mark.asyncio
async def test_retry_eventual_success(monkeypatch):
    sleep_calls = []
    async def mock_sleep(delay):
        sleep_calls.append(delay)
    monkeypatch.setattr("src.llm.client.asyncio.sleep", mock_sleep)
    
    adapter = TransientFailureAdapter(fail_count=2)
    client = LLMClient(adapter, concurrency=2, max_retries=3, timeout_seconds=5)
    
    req = LLMRequest(system_prompt="sys", user_prompt="usr", max_tokens=10, temperature=0.5)
    response = await client.complete(req)
    
    assert response.text == "eventual success"
    assert adapter.attempts == 3
    assert len(sleep_calls) == 2
    assert 0.5 <= sleep_calls[0] <= 0.7
    assert 1.0 <= sleep_calls[1] <= 1.2

@pytest.mark.asyncio
async def test_fail_fast_permanent_error():
    adapter = PermanentFailureAdapter()
    client = LLMClient(adapter, concurrency=2, max_retries=3, timeout_seconds=5)
    
    req = LLMRequest(system_prompt="sys", user_prompt="usr", max_tokens=10, temperature=0.5)
    with pytest.raises(PermanentLLMError) as exc_info:
        await client.complete(req)
        
    assert "Permanent error" in str(exc_info.value)
    assert adapter.attempts == 1

@pytest.mark.asyncio
async def test_timeout_treatment(monkeypatch):
    sleep_calls = []
    async def mock_sleep(delay):
        sleep_calls.append(delay)
    monkeypatch.setattr("src.llm.client.asyncio.sleep", mock_sleep)
    
    adapter = HangingFakeAdapter()
    client = LLMClient(adapter, concurrency=2, max_retries=2, timeout_seconds=0.01)
    
    req = LLMRequest(system_prompt="sys", user_prompt="usr", max_tokens=10, temperature=0.5)
    with pytest.raises(TransientLLMError) as exc_info:
        await client.complete(req)
        
    assert "timed out after" in str(exc_info.value)
    assert adapter.attempts == 3
    assert len(sleep_calls) == 2

def test_build_llm_client_and_registry(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-4")
    monkeypatch.setenv("LLM_CONCURRENCY", "4")
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "30.0")
    
    from config import get_settings
    get_settings.cache_clear()
    
    original_openai = PROVIDER_REGISTRY.pop("openai", None)
    try:
        client = build_llm_client()
        assert client.semaphore._value == 4
        assert client.max_retries == 2
        assert client.timeout_seconds == 30.0
        assert client.adapter.provider_name == "openai"
        assert "openai" in PROVIDER_REGISTRY
    finally:
        if original_openai:
            PROVIDER_REGISTRY["openai"] = original_openai

    monkeypatch.setenv("LLM_PROVIDER", "nonexistent_provider")
    get_settings.cache_clear()
    
    with pytest.raises(ValueError) as exc_info:
        build_llm_client()
    assert "Unknown LLM provider: 'nonexistent_provider'" in str(exc_info.value)

@pytest.mark.asyncio
async def test_privacy_logs_guard(caplog):
    class LoggingFakeAdapter:
        provider_name = "logging_fake"
        async def _call(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                text="CONFIDENTIAL_COMPLETION_SECRET_XYZ",
                usage=LLMUsage(input_tokens=10, output_tokens=15),
                model="log-model"
            )

    caplog.set_level("DEBUG")
    adapter = LoggingFakeAdapter()
    client = LLMClient(adapter, concurrency=1, max_retries=1, timeout_seconds=5)
    
    req = LLMRequest(
        system_prompt="sys prompt",
        user_prompt="TOP_SECRET_USER_CV_ABC",
        max_tokens=10,
        temperature=0.5
    )
    
    await client.complete(req)
    
    log_text = caplog.text
    assert "TOP_SECRET_USER_CV_ABC" not in log_text, "Logged prompt text!"
    assert "CONFIDENTIAL_COMPLETION_SECRET_XYZ" not in log_text, "Logged completion text!"
    assert "LLM complete success" in log_text
    assert "log-model" in log_text

def test_openai_and_google_import_boundary():
    src_dir = pathlib.Path(__file__).parent.parent / "src"
    
    for path in src_dir.rglob("*.py"):
        if path.name in ("openai_adapter.py", "google_adapter.py"):
            continue
        content = path.read_text(encoding="utf-8")
        assert "import openai" not in content, f"Unauthorized openai import in {path.name}"
        assert "from openai" not in content, f"Unauthorized openai import in {path.name}"
        assert "import google.genai" not in content, f"Unauthorized google-genai import in {path.name}"
        assert "from google" not in content, f"Unauthorized google import in {path.name}"
