"""
Reasoning settings, and the field-by-field inheritance the writer already uses.

A reasoning model rejects max_tokens and any non-default temperature, so the request
shape has to change. That is a per-profile fact, configured rather than guessed from the
model name: a name test would break on the next model OpenAI ships.

Hand-rolled fakes only. Nothing here makes a network call.
"""
import pytest

from config import get_settings, ConfigurationError

MAIN_KEY = "main-key-not-a-real-credential"
WRITER_KEY = "writer-key-not-a-real-credential"

WRITER_VARS = [
    "WRITER_LLM_PROVIDER",
    "WRITER_LLM_MODEL",
    "WRITER_LLM_API_KEY",
    "WRITER_LLM_BASE_URL",
    "WRITER_LLM_INPUT_TOKEN_RATE_USD",
    "WRITER_LLM_OUTPUT_TOKEN_RATE_USD",
    "WRITER_LLM_REASONING",
    "WRITER_LLM_REASONING_EFFORT",
]


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "main-model")
    monkeypatch.setenv("LLM_API_KEY", MAIN_KEY)
    for name in ["LLM_BASE_URL", "LLM_REASONING", "LLM_REASONING_EFFORT"] + WRITER_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def settings_now():
    get_settings.cache_clear()
    return get_settings()


def expect_failure():
    get_settings.cache_clear()
    with pytest.raises(ConfigurationError) as exc_info:
        get_settings()
    return str(exc_info.value)


# --- Defaults and inheritance ---

def test_reasoning_defaults_to_false_and_medium():
    settings = settings_now()
    for profile in (settings.main_llm_profile(), settings.writer_llm_profile()):
        assert profile.reasoning is False
        assert profile.reasoning_effort == "medium"


def test_reasoning_reaches_both_profiles(monkeypatch):
    monkeypatch.setenv("LLM_REASONING", "true")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
    settings = settings_now()

    for profile in (settings.main_llm_profile(), settings.writer_llm_profile()):
        assert profile.reasoning is True
        assert profile.reasoning_effort == "high"


def test_writer_inherits_reasoning_field_by_field(monkeypatch):
    monkeypatch.setenv("LLM_REASONING", "true")
    monkeypatch.setenv("WRITER_LLM_REASONING_EFFORT", "high")
    settings = settings_now()

    writer, main = settings.writer_llm_profile(), settings.main_llm_profile()
    assert writer.reasoning_effort == "high"
    assert main.reasoning_effort == "medium"
    assert writer.reasoning is True
    assert writer.model == main.model


def test_writer_can_turn_reasoning_off_explicitly(monkeypatch):
    """`is not None`, not `or`: false is an override, not an absence."""
    monkeypatch.setenv("LLM_REASONING", "true")
    monkeypatch.setenv("WRITER_LLM_REASONING", "false")
    settings = settings_now()

    assert settings.main_llm_profile().reasoning is True
    assert settings.writer_llm_profile().reasoning is False


# --- Validation ---

def test_reasoning_with_google_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google")
    monkeypatch.setenv("LLM_REASONING", "true")

    message = expect_failure()
    assert "LLM_REASONING" in message
    assert "'google'" in message


def test_writer_reasoning_with_google_raises(monkeypatch):
    monkeypatch.setenv("WRITER_LLM_PROVIDER", "google")
    monkeypatch.setenv("WRITER_LLM_API_KEY", WRITER_KEY)
    monkeypatch.setenv("WRITER_LLM_REASONING", "true")

    message = expect_failure()
    assert "'google'" in message
    assert "writer" in message


def test_google_without_reasoning_is_fine(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google")
    settings = settings_now()
    assert settings.main_llm_profile().reasoning is False


def test_invalid_effort_raises(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "extreme")
    message = expect_failure()
    assert "LLM_REASONING_EFFORT" in message
    for value in ("high", "low", "medium"):
        assert value in message


def test_invalid_writer_effort_raises(monkeypatch):
    monkeypatch.setenv("WRITER_LLM_REASONING_EFFORT", "exhaustive")
    message = expect_failure()
    assert "WRITER_LLM_REASONING_EFFORT" in message


def test_effort_set_while_reasoning_is_off_still_loads(monkeypatch, caplog):
    """Dormant, not fatal: it is logged and ignored."""
    caplog.set_level("INFO")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
    settings = settings_now()

    assert settings.main_llm_profile().reasoning is False
    assert settings.main_llm_profile().reasoning_effort == "high"
    assert "reasoning is off" in caplog.text
