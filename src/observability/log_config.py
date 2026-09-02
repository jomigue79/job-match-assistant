import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
import structlog

# Context variable for run ID tracking
RUN_ID_VAR: ContextVar[str | None] = ContextVar("run_id", default=None)
_is_configured = False

# Module-level variable to store the secret LLM API key value for string-based redactions
_llm_api_key_value: str | None = None

def redact_sensitive_processor(logger, method_name, event_dict):
    """
    structlog processor that redacts values for keys exactly matching:
    - password, secret, token, api_key, apikey, llm_api_key, access_token
    or ending with:
    - _key, _secret, _token, _password
    Also redacts any substring occurrences of the cached LLM API key.
    """
    sensitive_keys = {
        "password", "secret", "token", "api_key", "apikey",
        "llm_api_key", "access_token"
    }
    sensitive_suffixes = ("_key", "_secret", "_token", "_password")

    def redact_val(val):
        if isinstance(val, dict):
            return {k: redact_val(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [redact_val(item) for item in val]
        elif isinstance(val, str):
            if _llm_api_key_value and _llm_api_key_value in val:
                val = val.replace(_llm_api_key_value, "[REDACTED_API_KEY]")
            return val
        return val

    new_event = {}
    for k, v in event_dict.items():
        k_lower = k.lower()
        is_sensitive = (
            k_lower in sensitive_keys or
            k_lower.endswith(sensitive_suffixes)
        )
        if is_sensitive:
            new_event[k] = "[REDACTED]"
        else:
            new_event[k] = redact_val(v)

    if "event" in new_event and isinstance(new_event["event"], str):
        evt = new_event["event"]
        if _llm_api_key_value and _llm_api_key_value in evt:
            new_event["event"] = evt.replace(_llm_api_key_value, "[REDACTED_API_KEY]")

    return new_event

def setup_logging():
    """
    Sets up stdlib logging handlers (Console and Rotating File) and integrates them with structlog.
    """
    global _is_configured, _llm_api_key_value
    if _is_configured:
        return

    # Default settings fallback
    log_level_str = "INFO"
    log_file_path = "data/app.log"

    try:
        from config import get_settings
        settings = get_settings()
        log_level_str = settings.log_level
        log_file_path = settings.log_file_path
        
        # Capture the secret LLM API key ONCE during setup
        if settings.llm_api_key:
            _llm_api_key_value = settings.llm_api_key.get_secret_value()
    except Exception:
        # Gracefully handle config loading failures
        _llm_api_key_value = None

    level = getattr(logging, log_level_str.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    # Console Handler (Stream to stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=False),
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
        ]
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # Rotating File Handler
    if log_file_path:
        Path(log_file_path).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
            ]
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Configure structlog processors pipeline
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_sensitive_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    _is_configured = True

def get_logger(component: str):
    """
    Returns a structlog BoundLogger with the component field bound to the log context.
    """
    if not _is_configured:
        setup_logging()
    return structlog.get_logger().bind(component=component)

@contextmanager
def bind_run(run_id: str):
    """
    Context manager that binds a run_id to the logging context for the duration of a run.
    """
    token = RUN_ID_VAR.set(run_id)
    structlog.contextvars.bind_contextvars(run_id=run_id)
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars("run_id")
        RUN_ID_VAR.reset(token)
