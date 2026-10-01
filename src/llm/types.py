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

class LLMUsage(BaseModel):
    """
    Model tracking input and output token consumption for a single LLM request.
    """
    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int

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
