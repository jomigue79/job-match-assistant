from typing import Optional

from pydantic import BaseModel, ConfigDict, SecretStr

class LLMRequest(BaseModel):
    """
    Model representing system prompts, user prompts, and basic temperature/max_tokens parameters for a completion call.
    """
    model_config = ConfigDict(frozen=True)

    system_prompt: str
    user_prompt: str
    max_tokens: int
    temperature: float
    json_mode: bool = False
    # Per-skill override for a reasoning model. None takes the profile's effort.
    # The scorer extracts five numbers from a rubric and asks for low; the writer
    # leaves it unset. Ignored entirely when the profile is not a reasoning one.
    reasoning_effort: Optional[str] = None

class LLMProfile(BaseModel):
    """
    One provider's complete call configuration: credentials, endpoint, model, and the
    token rates its cost estimate uses. Adapters receive a profile instead of reading
    settings, so the writer and the scorer can run on different providers and models.
    """
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    api_key: Optional[SecretStr] = None
    base_url: Optional[str] = None
    input_token_rate_usd: float
    output_token_rate_usd: float
    # Reasoning models reject max_tokens and any non-default temperature, so the
    # adapter sends a different request shape. False keeps the ordinary shape, which
    # is what Gemini and ordinary OpenAI chat models need.
    reasoning: bool = False
    reasoning_effort: str = "medium"

class LLMUsage(BaseModel):
    """
    Model tracking input and output token consumption for a single LLM request.
    """
    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int
    # Already counted inside output_tokens by the provider: reported for visibility,
    # never added to a cost sum, or reasoning would be charged twice.
    reasoning_tokens: Optional[int] = None

class LLMResponse(BaseModel):
    """
    Model packing raw returned text, final token usage, and the model name used in processing.
    """
    model_config = ConfigDict(frozen=True)

    text: str
    usage: LLMUsage
    model: str

# Custom LLM exception boundaries
class LLMError(Exception):
    """Base exception for all LLM client and provider API errors."""
    pass

class TransientLLMError(LLMError):
    """Exception raised for transient (retryable) failures (timeouts, rate limits, server 5xx)."""
    pass

class PermanentLLMError(LLMError):
    """Exception raised for permanent (fail-fast) failures (auth, bad requests, invalid options)."""
    pass
