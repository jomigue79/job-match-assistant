import asyncio
import time
import random
from typing import Optional

from config import get_settings
from observability import get_logger, CostAccumulator
from .types import LLMRequest, LLMResponse, LLMUsage, LLMError, TransientLLMError, PermanentLLMError
from .adapter import ProviderAdapter, get_adapter

logger = get_logger("llm_client")

class LLMClient:
    """
    LLM Client orchestrating text completions over a concrete provider adapter.
    Enforces concurrency bounds, attempt timeouts, and retry backoffs.
    """
    def __init__(
        self,
        adapter: ProviderAdapter,
        concurrency: int,
        max_retries: int,
        timeout_seconds: float
    ):
        self.adapter = adapter
        self.semaphore = asyncio.Semaphore(concurrency)
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        request: LLMRequest,
        cost_accumulator: Optional[CostAccumulator] = None
    ) -> LLMResponse:
        """
        Executes an LLM request under concurrency bounds, timeouts, and rate limits.
        """
        attempt = 1
        
        async with self.semaphore:
            while True:
                start_time = time.perf_counter()
                try:
                    # Execute call wrapped in request attempt timeout
                    response = await asyncio.wait_for(
                        self.adapter._call(request),
                        timeout=self.timeout_seconds
                    )
                    latency = time.perf_counter() - start_time
                    
                    # Structured log ONLY metadata, never prompt or completion text (privacy guard)
                    logger.info(
                        "LLM complete success",
                        model=response.model,
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        latency_seconds=latency,
                        attempt=attempt
                    )
                    
                    # Record cost if accumulator is provided
                    if cost_accumulator is not None:
                        cost_accumulator.add_llm_usage(
                            input_tokens=response.usage.input_tokens,
                            output_tokens=response.usage.output_tokens,
                            calls=1
                        )
                        
                    return response
                    
                except asyncio.TimeoutError as e:
                    latency = time.perf_counter() - start_time
                    logger.warning(
                        "LLM attempt timeout",
                        attempt=attempt,
                        timeout_seconds=self.timeout_seconds,
                        latency_seconds=latency
                    )
                    if attempt > self.max_retries:
                        raise TransientLLMError(f"LLM request timed out after {self.max_retries} attempts.") from e
                        
                except TransientLLMError as e:
                    latency = time.perf_counter() - start_time
                    logger.warning(
                        "LLM attempt transient failure",
                        attempt=attempt,
                        error=str(e),
                        latency_seconds=latency
                    )
                    if attempt > self.max_retries:
                        raise
                        
                except PermanentLLMError as e:
                    latency = time.perf_counter() - start_time
                    logger.error(
                        "LLM attempt permanent failure",
                        attempt=attempt,
                        error=str(e),
                        latency_seconds=latency
                    )
                    raise
                    
                except Exception as e:
                    latency = time.perf_counter() - start_time
                    logger.error(
                        "LLM attempt unexpected failure",
                        attempt=attempt,
                        error=str(e),
                        latency_seconds=latency
                    )
                    raise PermanentLLMError(f"Unexpected error in LLM client: {e}") from e
                
                # Exponential backoff base 0.5s + jitter
                backoff = 0.5 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.2)
                await asyncio.sleep(backoff)
                attempt += 1


def build_llm_client() -> LLMClient:
    """
    Factory constructing an LLMClient instance wired from configuration.
    """
    settings = get_settings()
    adapter = get_adapter(settings.llm_provider)
    
    return LLMClient(
        adapter=adapter,
        concurrency=settings.llm_concurrency,
        max_retries=settings.llm_max_retries,
        timeout_seconds=settings.llm_timeout_seconds
    )
