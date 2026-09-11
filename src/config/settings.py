from functools import lru_cache
from pathlib import Path
from pydantic import Field, SecretStr, field_validator, ValidationError, ValidationInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

class ConfigurationError(ValueError):
    """Raised when application configuration is missing or invalid."""
    pass

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # Paths (defaults located under a project data directory)
    db_path: str = Field(default="data/app.db", validation_alias="DATABASE_PATH")
    raw_scrape_dir: str = Field(default="data/raw_scrapes", validation_alias="RAW_SCRAPE_DIR")
    ledger_xlsx_path: str = Field(default="data/ledger.xlsx", validation_alias="LEDGER_XLSX_PATH")
    google_service_account_path: str = Field(default="secrets/service_account.json", validation_alias="GOOGLE_SERVICE_ACCOUNT_PATH")
    ledger_sheet_id: str | None = Field(default=None, validation_alias="LEDGER_SHEET_ID")
    knowledge_dir: str = Field(default="data/knowledge", validation_alias="KNOWLEDGE_DIR")
    log_file_path: str = Field(default="data/app.log", validation_alias="LOG_FILE_PATH")
    
    # Source / Scraper Configuration
    scraper_source: str = Field(..., validation_alias="SCRAPER_SOURCE")
    # Declared before scraper_query: its validator reads this value from info.data.
    scraper_max_queries: int = Field(default=5, validation_alias="SCRAPER_MAX_QUERIES")
    # Comma-separated job titles; each title is a separate Apify Actor Start.
    scraper_query: str = Field(default="Software Engineer", validation_alias="SCRAPER_QUERY")
    scraper_location: str = Field(default="Remote", validation_alias="SCRAPER_LOCATION")
    scraper_limit: int = Field(default=20, validation_alias="SCRAPER_LIMIT")
    scraper_country: str | None = Field(default=None, validation_alias="SCRAPER_COUNTRY")
    scraper_posted_since: str | None = Field(default=None, validation_alias="SCRAPER_POSTED_SINCE")
    apify_api_token: SecretStr | None = Field(default=None, validation_alias="APIFY_API_TOKEN")
    apify_actor_id: str = Field(default="apify/linkedin-jobs-scraper", validation_alias="APIFY_ACTOR_ID")
    replay_from_cache: bool = Field(default=False, validation_alias="REPLAY_FROM_CACHE")
    replay_cache_path: str | None = Field(default=None, validation_alias="REPLAY_CACHE_PATH")
    
    # LLM Settings (provider must be plain data string, e.g. "openai" / "anthropic")
    llm_provider: str = Field(default="openai", validation_alias="LLM_PROVIDER")
    llm_model: str = Field(default="gpt-4o", validation_alias="LLM_MODEL")
    llm_api_key: SecretStr = Field(..., validation_alias="LLM_API_KEY")
    llm_base_url: str | None = Field(default=None, validation_alias="LLM_BASE_URL")
    llm_max_retries: int = Field(default=3, validation_alias="LLM_MAX_RETRIES")
    llm_timeout_seconds: float = Field(default=60.0, validation_alias="LLM_TIMEOUT_SECONDS")
    
    # Scoring Config
    score_threshold: int = Field(default=70, validation_alias="SCORE_THRESHOLD")
    
    # Cost & Rate Settings (Rough Estimates)
    llm_input_token_rate_usd: float = Field(default=5.00 / 1_000_000, validation_alias="LLM_INPUT_TOKEN_RATE_USD")
    llm_output_token_rate_usd: float = Field(default=15.00 / 1_000_000, validation_alias="LLM_OUTPUT_TOKEN_RATE_USD")
    apify_cu_rate_usd: float = Field(default=0.25, validation_alias="APIFY_CU_RATE_USD")

    # Pre-run Apify cost projection: one Actor Start per query plus its expected results.
    apify_actor_start_usd: float = Field(default=0.01, validation_alias="APIFY_ACTOR_START_USD")
    apify_result_usd: float = Field(default=0.003, validation_alias="APIFY_RESULT_USD")
    # Records returned per unit of SCRAPER_LIMIT. The actor applies the limit per platform,
    # so a query returns more records than its limit. Calibrated from nine envelopes (2026-09,
    # all at limit 10, 35-45 records per query: 3.5-4.5); the default is the observed maximum
    # so the projection does not under-report. Actor-specific, adjust if projections drift.
    scraper_results_per_limit: float = Field(default=4.5, validation_alias="SCRAPER_RESULTS_PER_LIMIT")
    # A run whose projected Apify cost exceeds this is refused and recorded as failed.
    run_budget_cap_usd: float = Field(default=2.00, validation_alias="RUN_BUDGET_CAP_USD")

    # Runtime Config
    llm_concurrency: int = Field(default=5, validation_alias="LLM_CONCURRENCY")
    sqlite_busy_timeout_ms: int = Field(default=5000, validation_alias="SQLITE_BUSY_TIMEOUT_MS")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    ui_host: str = Field(default="127.0.0.1", validation_alias="UI_HOST")
    ui_port: int = Field(default=8080, validation_alias="UI_PORT")
    ui_exit_grace_seconds: float = Field(default=5.0, validation_alias="UI_EXIT_GRACE_SECONDS")

    @field_validator("score_threshold")
    @classmethod
    def validate_score_threshold(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError("score_threshold must be between 0 and 100")
        return v

    @field_validator("llm_concurrency")
    @classmethod
    def validate_llm_concurrency(cls, v: int) -> int:
        if v < 1:
            raise ValueError("llm_concurrency must be at least 1")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {sorted(valid_levels)}")
        return v.upper()

    @field_validator("scraper_max_queries")
    @classmethod
    def validate_scraper_max_queries(cls, v: int) -> int:
        if v < 1:
            raise ValueError("scraper_max_queries must be at least 1")
        return v

    @field_validator("scraper_query")
    @classmethod
    def validate_scraper_query(cls, v: str, info: ValidationInfo) -> str:
        titles = [t.strip() for t in v.split(",") if t.strip()]
        if not titles:
            raise ValueError("SCRAPER_QUERY must name at least one job title")
        folded = [t.casefold() for t in titles]
        if len(set(folded)) != len(folded):
            raise ValueError("SCRAPER_QUERY lists the same job title more than once")
        # Absent when SCRAPER_MAX_QUERIES itself failed validation; that error is reported on its own.
        max_queries = info.data.get("scraper_max_queries")
        if max_queries is not None and len(titles) > max_queries:
            raise ValueError(
                f"SCRAPER_QUERY lists {len(titles)} job titles; SCRAPER_MAX_QUERIES allows {max_queries}"
            )
        return ",".join(titles)

    @field_validator("run_budget_cap_usd")
    @classmethod
    def validate_run_budget_cap_usd(cls, v: float) -> float:
        if v < 0:
            raise ValueError("run_budget_cap_usd must not be negative")
        return v

    @property
    def scraper_queries(self) -> list[str]:
        """SCRAPER_QUERY as a list of job titles, already stripped and validated."""
        return self.scraper_query.split(",")

@lru_cache()
def get_settings(_env_file: str | None = ".env") -> Settings:
    """
    Returns a validated, cached Settings instance.
    Fails fast on missing required variables or invalid types/values.
    """
    try:
        settings = Settings(_env_file=_env_file)
        
        # Ensure parent directories exist for writable artifacts
        for p in [settings.db_path, settings.ledger_xlsx_path, settings.log_file_path]:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            
        # Ensure directories exist
        for d in [settings.raw_scrape_dir, settings.knowledge_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)
            
        return settings
    except ValidationError as e:
        missing_keys = []
        validation_failures = []
        for error in e.errors():
            loc = error.get("loc", ())
            field_name = str(loc[0]) if loc else "unknown"
            
            if error.get("type") == "missing":
                missing_keys.append(field_name)
            else:
                msg = error.get("msg", "invalid value")
                if msg.startswith("Value error, "):
                    msg = msg[len("Value error, "):]
                validation_failures.append(f"{field_name}: {msg}")
                
        err_messages = []
        if missing_keys:
            err_messages.append(f"Missing required configuration key(s): {', '.join(missing_keys)}")
        if validation_failures:
            err_messages.append(f"Validation failure(s) - {'; '.join(validation_failures)}")
            
        raise ConfigurationError("\n".join(err_messages)) from e
