import openai
from .types import LLMProfile, LLMRequest, LLMResponse, LLMUsage, TransientLLMError, PermanentLLMError
from .adapter import register_provider

# A reasoning model spends part of its completion budget on hidden reasoning, so a
# cap sized for prose can return an empty message. This floor applies only to the
# reasoning shape; the request's own value wins when it is larger.
REASONING_TOKEN_FLOOR = 8000

class OpenAIAdapter:
    """
    OpenAI implementation of ProviderAdapter mapping completion requests and handling SDK errors.
    """
    provider_name = "openai"

    def __init__(self, profile: LLMProfile):
        # The profile is the only source of credentials, endpoint and model: this
        # adapter reads no settings, so two profiles can run side by side.
        self.profile = profile
        api_key = profile.api_key.get_secret_value() if profile.api_key else None

        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=profile.base_url
        )
        self.model = profile.model

    async def _call(self, request: LLMRequest) -> LLMResponse:
        messages = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt}
        ]
        
        kwargs = {
            "model": self.model,
            "messages": messages,
        }

        if self.profile.reasoning:
            # max_tokens and any non-default temperature are rejected outright:
            # "Use 'max_completion_tokens' instead", "Only the default (1) value is
            # supported". Temperature is omitted rather than set to 1.
            kwargs["max_completion_tokens"] = max(request.max_tokens, REASONING_TOKEN_FLOOR)
            kwargs["reasoning_effort"] = request.reasoning_effort or self.profile.reasoning_effort
        else:
            kwargs["max_tokens"] = request.max_tokens
            kwargs["temperature"] = request.temperature
        
        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            chat_completion = await self.client.chat.completions.create(**kwargs)
            text = chat_completion.choices[0].message.content or ""
            
            raw_usage = chat_completion.usage
            reasoning_tokens = None
            if raw_usage:
                input_tokens = raw_usage.prompt_tokens
                output_tokens = raw_usage.completion_tokens
                # Absent on ordinary chat models and on older SDKs: read defensively.
                details = getattr(raw_usage, "completion_tokens_details", None)
                reasoning_tokens = getattr(details, "reasoning_tokens", None) if details is not None else None
            else:
                input_tokens = 0
                output_tokens = 0

            return LLMResponse(
                text=text,
                usage=LLMUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens
                ),
                model=self.model
            )
            
        except openai.APITimeoutError as e:
            raise TransientLLMError(f"OpenAI Timeout: {e}") from e
        except openai.RateLimitError as e:
            raise TransientLLMError(f"OpenAI Rate Limit: {e}") from e
        except openai.APIConnectionError as e:
            raise TransientLLMError(f"OpenAI Connection Error: {e}") from e
        except openai.APIStatusError as e:
            if e.status_code >= 500:
                raise TransientLLMError(f"OpenAI Server Error ({e.status_code}): {e}") from e
            else:
                raise PermanentLLMError(f"OpenAI Permanent Error ({e.status_code}): {e}") from e
        except Exception as e:
            raise PermanentLLMError(f"OpenAI Unexpected Error: {e}") from e

# Register self to provider registry
register_provider("openai", OpenAIAdapter)
