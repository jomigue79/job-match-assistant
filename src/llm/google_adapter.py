from google import genai
from google.genai import types, errors
from config import get_settings
from .types import LLMRequest, LLMResponse, LLMUsage, TransientLLMError, PermanentLLMError
from .adapter import register_provider

class GoogleAdapter:
    """
    Google Gemini implementation of ProviderAdapter mapping completion requests and handling SDK errors.
    """
    provider_name = "google"

    def __init__(self):
        settings = get_settings()
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        
        self.client = genai.Client(
            api_key=api_key
        )
        self.model = settings.llm_model

    async def _call(self, request: LLMRequest) -> LLMResponse:
        config_kwargs = {
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens,
            "system_instruction": request.system_prompt,
            "thinking_config": types.ThinkingConfig(thinking_budget=0)
        }
        if request.json_mode:
            config_kwargs["response_mime_type"] = "application/json"
            
        config = types.GenerateContentConfig(**config_kwargs)
        
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=request.user_prompt,
                config=config
            )
            
            try:
                text = response.text or ""
            except ValueError:
                text = ""
            
            input_tokens = 0
            output_tokens = 0
            if response.usage_metadata:
                input_tokens = response.usage_metadata.prompt_token_count or 0
                output_tokens = getattr(response.usage_metadata, 'candidates_token_count', None) or getattr(response.usage_metadata, 'response_token_count', None) or 0
                
            return LLMResponse(
                text=text,
                usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
                model=self.model
            )
            
        except errors.ServerError as e:
            raise TransientLLMError(f"Gemini Server Error: {e}") from e
        except errors.ClientError as e:
            if e.code == 429:
                raise TransientLLMError(f"Gemini Rate Limit: {e}") from e
            raise PermanentLLMError(f"Gemini Client Error: {e}") from e
        except errors.APIError as e:
            if e.code == 429:
                raise TransientLLMError(f"Gemini Rate Limit: {e}") from e
            if e.code and e.code >= 500:
                raise TransientLLMError(f"Gemini Server Error: {e}") from e
            raise PermanentLLMError(f"Gemini API Error: {e}") from e
        except Exception as e:
            raise PermanentLLMError(f"Gemini Unexpected Error: {e}") from e

# Register self to provider registry
register_provider("google", GoogleAdapter)
