import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .log_config import bind_run

class RunCostSummary(BaseModel):
    """
    Representational metrics capture model for LLM and Scraper pipeline run costs.
    """
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_llm_calls: int = 0
    apify_compute_units: float = 0.0
    apify_results: int = 0
    estimated_cost_usd: float = 0.0

class CostAccumulator:
    """
    Helper accumulator compiling raw token usages and compute units
    during run execution phases. Supports injectable rates for testing.
    """
    def __init__(
        self,
        llm_input_token_rate_usd: Optional[float] = None,
        llm_output_token_rate_usd: Optional[float] = None,
        apify_cu_rate_usd: Optional[float] = None
    ):
        # Resolve defaults lazily to avoid module-level circular imports
        default_input = 5.00 / 1_000_000
        default_output = 15.00 / 1_000_000
        default_apify = 0.25

        try:
            from config import get_settings
            settings = get_settings()
            default_input = settings.llm_input_token_rate_usd
            default_output = settings.llm_output_token_rate_usd
            default_apify = settings.apify_cu_rate_usd
        except Exception:
            pass

        self.llm_input_token_rate_usd = (
            llm_input_token_rate_usd if llm_input_token_rate_usd is not None else default_input
        )
        self.llm_output_token_rate_usd = (
            llm_output_token_rate_usd if llm_output_token_rate_usd is not None else default_output
        )
        self.apify_cu_rate_usd = (
            apify_cu_rate_usd if apify_cu_rate_usd is not None else default_apify
        )

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_llm_calls = 0
        self.apify_compute_units = 0.0
        self.apify_results = 0

    def add_llm_usage(self, input_tokens: int, output_tokens: int, calls: int = 1):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_llm_calls += calls

    def add_apify_usage(self, compute_units: float = 0.0, results: int = 0):
        self.apify_compute_units += compute_units
        self.apify_results += results

    def summary(self) -> RunCostSummary:
        estimated_cost = (
            self.total_input_tokens * self.llm_input_token_rate_usd +
            self.total_output_tokens * self.llm_output_token_rate_usd +
            self.apify_compute_units * self.apify_cu_rate_usd
        )
        return RunCostSummary(
            total_input_tokens=self.total_input_tokens,
            total_output_tokens=self.total_output_tokens,
            total_llm_calls=self.total_llm_calls,
            apify_compute_units=self.apify_compute_units,
            apify_results=self.apify_results,
            estimated_cost_usd=round(estimated_cost, 6)
        )

class RunReport(BaseModel):
    """
    Performance and diagnostic metadata capturing object for scraper runs.
    """
    run_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    source: str

    # Performance counts
    n_scraped: int = 0
    n_new: int = 0
    n_matched: int = 0
    n_no_match: int = 0

    # Diagnostic logs
    errors: List[Dict[str, Any]] = Field(default_factory=list)

    # Cost totals
    cost: RunCostSummary = Field(default_factory=RunCostSummary)

    def record_error(self, component: str, message: str, **context):
        self.errors.append({
            "component": component,
            "message": message,
            "context": context
        })

    def finalize(self):
        self.finished_at = datetime.now(timezone.utc)

@contextmanager
def start_run_report(source: str):
    """
    Initializes a new RunReport context bound to the logging execution scope.
    Guarantees finalization on context block exit.
    """
    run_id = str(uuid.uuid4())
    report = RunReport(
        run_id=run_id,
        started_at=datetime.now(timezone.utc),
        source=source,
        cost=RunCostSummary()
    )
    try:
        with bind_run(run_id):
            yield report
    finally:
        report.finalize()
