import pytest
from pydantic import ValidationError
from config.settings import Settings, ConfigurationError, get_settings

def test_valid_settings_loading(monkeypatch):
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "test-key-12345")
    monkeypatch.setenv("DATABASE_PATH", "data/test_app.db")
    monkeypatch.setenv("SCORE_THRESHOLD", "80")
    monkeypatch.setenv("LLM_CONCURRENCY", "10")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    
    settings = Settings(_env_file=None)
    assert settings.scraper_source == "apify_linkedin"
    assert settings.llm_api_key.get_secret_value() == "test-key-12345"
    assert settings.db_path == "data/test_app.db"
    assert settings.score_threshold == 80
    assert settings.llm_concurrency == 10
    assert settings.log_level == "DEBUG"

def test_missing_required_key(monkeypatch):
    monkeypatch.delenv("SCRAPER_SOURCE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    
    get_settings.cache_clear()
    
    with pytest.raises(ConfigurationError) as exc_info:
        get_settings(_env_file=None)
        
    err_msg = str(exc_info.value)
    assert "Missing required configuration key(s)" in err_msg
    assert "SCRAPER_SOURCE" in err_msg
    assert "LLM_API_KEY" in err_msg

def test_invalid_score_threshold(monkeypatch):
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "test-key-12345")
    
    # Invalid too high
    monkeypatch.setenv("SCORE_THRESHOLD", "101")
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert "score_threshold" in str(exc_info.value)
        
    # Invalid too low
    monkeypatch.setenv("SCORE_THRESHOLD", "-5")
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert "score_threshold" in str(exc_info.value)

def test_invalid_llm_concurrency(monkeypatch):
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_CONCURRENCY", "0")
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert "llm_concurrency" in str(exc_info.value)

def test_invalid_log_level(monkeypatch):
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "test-key-12345")
    monkeypatch.setenv("LOG_LEVEL", "INVALID_LOG_LEVEL")
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    assert "log_level" in str(exc_info.value)

def test_get_settings_creates_directories(monkeypatch, tmp_path):
    db_file = tmp_path / "subdir" / "test.db"
    scrape_dir = tmp_path / "raw_crawls"
    ledger_file = tmp_path / "ledger" / "sheet.xlsx"
    knowledge_dir = tmp_path / "knowledge_artifacts"
    
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "test-key-12345")
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    monkeypatch.setenv("RAW_SCRAPE_DIR", str(scrape_dir))
    monkeypatch.setenv("LEDGER_XLSX_PATH", str(ledger_file))
    monkeypatch.setenv("KNOWLEDGE_DIR", str(knowledge_dir))
    
    get_settings.cache_clear()
    settings = get_settings(_env_file=None)
    
    # Check parent dirs are created
    assert db_file.parent.exists()
    assert ledger_file.parent.exists()
    # Check dirs themselves are created
    assert scrape_dir.exists()
    assert knowledge_dir.exists()

def test_get_settings_invalid_score_threshold(monkeypatch):
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "test-key-12345")
    monkeypatch.setenv("SCORE_THRESHOLD", "150") # Out of bounds (>100) (FIX 6)
    
    get_settings.cache_clear()
    
    with pytest.raises(ConfigurationError) as exc_info:
        get_settings(_env_file=None)
        
    err_msg = str(exc_info.value)
    assert "score_threshold" in err_msg

def test_google_sheets_config_loading(monkeypatch):
    monkeypatch.setenv("SCRAPER_SOURCE", "apify_linkedin")
    monkeypatch.setenv("LLM_API_KEY", "test-key-12345")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_PATH", "secrets/my_service_account.json")
    monkeypatch.setenv("LEDGER_SHEET_ID", "my_google_sheet_id_123")
    
    get_settings.cache_clear()
    settings = get_settings(_env_file=None)
    assert settings.google_service_account_path == "secrets/my_service_account.json"
    assert settings.ledger_sheet_id == "my_google_sheet_id_123"
