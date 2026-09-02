from typing import Dict, Protocol, Type
from .types import LLMRequest, LLMResponse

class ProviderAdapter(Protocol):
    """
    Protocol definition for provider-specific API call mapping.
    """
    @property
    def provider_name(self) -> str:
        """Name identification of the LLM provider."""
        ...

    async def _call(self, request: LLMRequest) -> LLMResponse:
        """
        Executes raw SDK completion calls, mapping errors to transient vs permanent.
        """
        ...

# Central registry holding provider constructor types
PROVIDER_REGISTRY: Dict[str, Type[ProviderAdapter]] = {}

def register_provider(provider_name: str, adapter_cls: Type[ProviderAdapter]) -> None:
    """Registers a concrete provider adapter class."""
    PROVIDER_REGISTRY[provider_name] = adapter_cls

def get_adapter(provider_name: str) -> ProviderAdapter:
    """
    Looks up and instantiates the registered adapter.
    Raises ValueError on unknown provider name.
    """
    if provider_name == "openai" and "openai" not in PROVIDER_REGISTRY:
        from .openai_adapter import OpenAIAdapter
        PROVIDER_REGISTRY["openai"] = OpenAIAdapter
    elif provider_name == "google" and "google" not in PROVIDER_REGISTRY:
        from .google_adapter import GoogleAdapter
        PROVIDER_REGISTRY["google"] = GoogleAdapter

    if provider_name not in PROVIDER_REGISTRY:
        raise ValueError(
            f"Unknown LLM provider: '{provider_name}'. "
            f"Registered providers: {list(PROVIDER_REGISTRY.keys())}"
        )
    return PROVIDER_REGISTRY[provider_name]()
