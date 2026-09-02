from pydantic import BaseModel, ConfigDict

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
