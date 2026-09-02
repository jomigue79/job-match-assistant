import json
import pytest
from observability import (
    bind_run,
    get_logger,
    CostAccumulator,
    RunCostSummary,
    RunReport,
    start_run_report,
)

def test_logging_to_file(monkeypatch, tmp_path):
    log_file = tmp_path / "test_run.log"
    
    # Set required settings and log path via environment variables
    monkeypatch.setenv("LOG_FILE_PATH", str(log_file))
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "super-secret-api-key-999")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    
    # Force setup_logging to re-read config and configure handlers
    from config import get_settings
    get_settings.cache_clear()
    import observability.log_config as obs_logging
    obs_logging._is_configured = False
    
    logger = get_logger("my_component")
    
    # Log outside run context
    logger.info("outside_event", user_password="secret123")
    
    # Log inside run context
    with bind_run("run_9999"):
        logger.info("inside_event", plain_field="hello_world")
        logger.info("leak_event", msg="API key value is super-secret-api-key-999!")
        
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    records = [json.loads(line) for line in lines if line]
    
    # Match record 1 (outside_event)
    r1 = next(r for r in records if r.get("event") == "outside_event")
    assert r1["component"] == "my_component"
    assert r1["level"] == "info"
    assert r1["user_password"] == "[REDACTED]"
    assert "run_id" not in r1
    
    # Match record 2 (inside_event)
    r2 = next(r for r in records if r.get("event") == "inside_event")
    assert r2["component"] == "my_component"
    assert r2["plain_field"] == "hello_world"
    assert r2["run_id"] == "run_9999"
    
    # Match record 3 (leak_event)
    r3 = next(r for r in records if r.get("event") == "leak_event")
    assert "super-secret-api-key-999" not in r3["msg"]
    assert "[REDACTED_API_KEY]" in r3["msg"]

def test_redaction_tight_rules(monkeypatch, tmp_path):
    log_file = tmp_path / "redaction_test.log"
    monkeypatch.setenv("LOG_FILE_PATH", str(log_file))
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "super-secret-api-key-999")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    
    from config import get_settings
    get_settings.cache_clear()
    import observability.log_config as obs_logging
    obs_logging._is_configured = False
    
    logger = get_logger("my_component")
    logger.info(
        "redact_check",
        keyword="python",          # must be preserved (FIX 3)
        api_key="my-secret-key",    # must be redacted
        password="my-password",      # must be redacted
        monkey_count=10             # must be preserved
    )
    
    assert log_file.exists()
    records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").strip().split("\n") if line]
    r = next(rec for rec in records if rec.get("event") == "redact_check")
    assert r["keyword"] == "python"
    assert r["monkey_count"] == 10
    assert r["api_key"] == "[REDACTED]"
    assert r["password"] == "[REDACTED]"

def test_cost_accumulator():
    # Inject known explicit rates (FIX 5)
    acc = CostAccumulator(
        llm_input_token_rate_usd=0.01,
        llm_output_token_rate_usd=0.02,
        apify_cu_rate_usd=0.10
    )
    acc.add_llm_usage(input_tokens=100, output_tokens=50, calls=2)
    acc.add_apify_usage(compute_units=5.0, results=10)
    
    summary = acc.summary()
    assert summary.total_input_tokens == 100
    assert summary.total_output_tokens == 50
    assert summary.total_llm_calls == 2
    assert summary.apify_compute_units == 5.0
    assert summary.apify_results == 10
    
    # estimated_cost_usd = 100 * 0.01 + 50 * 0.02 + 5.0 * 0.10 = 1.0 + 1.0 + 0.5 = 2.5
    assert summary.estimated_cost_usd == 2.5

def test_run_report_lifecycle():
    with start_run_report("apify_linkedin") as report:
        assert report.run_id is not None
        assert report.started_at is not None
        assert report.finished_at is None
        assert report.source == "apify_linkedin"
        
        # Initial counts
        assert report.n_scraped == 0
        assert report.n_new == 0
        assert report.n_matched == 0
        assert report.n_no_match == 0
        
        # Update counts
        report.n_scraped = 5
        report.n_new = 3
        
        # Record errors
        report.record_error("Scraper", "Timeout connecting to Apify", timeout=30)
        assert len(report.errors) == 1
        assert report.errors[0]["component"] == "Scraper"
        assert report.errors[0]["message"] == "Timeout connecting to Apify"
        assert report.errors[0]["context"] == {"timeout": 30}
        
        # Record cost summary
        cost_acc = CostAccumulator()
        cost_acc.add_llm_usage(input_tokens=200, output_tokens=100)
        report.cost = cost_acc.summary()
        
        report.finalize()
        
    assert report.finished_at is not None
    assert report.finished_at >= report.started_at
    assert report.cost.total_input_tokens == 200

def test_run_report_exception_finalizes():
    # start_run_report finalize try/finally check (FIX 6)
    report_ref = None
    try:
        with start_run_report("apify_linkedin") as report:
            report_ref = report
            assert report.finished_at is None
            raise RuntimeError("Simulated error")
    except RuntimeError:
        pass
        
    assert report_ref is not None
    assert report_ref.finished_at is not None
