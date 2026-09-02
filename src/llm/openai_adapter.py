import openai
from config import get_settings
from .types import LLMRequest, LLMResponse, LLMUsage, TransientLLMError, PermanentLLMError
from .adapter import register_provider

class OpenAIAdapter:
    """
    OpenAI implementation of ProviderAdapter mapping completion requests and handling SDK errors.
    """
    provider_name = "openai"

    def __init__(self):
        settings = get_settings()
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=settings.llm_base_url
        )
        self.model = settings.llm_model

    async def _call(self, request: LLMRequest) -> LLMResponse:
        messages = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt}
        ]
        
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        
        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            chat_completion = await self.client.chat.completions.create(**kwargs)
            text = chat_completion.choices[0].message.content or ""
            
            raw_usage = chat_completion.usage
            if raw_usage:
                input_tokens = raw_usage.prompt_tokens
                output_tokens = raw_usage.completion_tokens
            else:
                input_tokens = 0
                output_tokens = 0
                
            return LLMResponse(
                text=text,
                usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
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
