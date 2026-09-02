# Configuration & Secrets Control Reference

This document provides a detailed reference for all configuration options supported by the Job Match Assistant. Runtime configuration is handled via environment variables or a local `.env` file, loaded safely and validated at boot-time via `pydantic-settings`.

---

## Configuration Variables

### Required Configs (App fails fast if missing)

| Env Key | Description | Type / Constraints |
| :--- | :--- | :--- |
| `SCRAPER_SOURCE` | The identifier for the web scraper adapter and Apify Actor module. | `str` (non-empty) |
| `APIFY_API_TOKEN` | Secret token for Apify SDK integration (SecretStr, defaults to None). Optional to parse config but required to execute ApifyScraperClient. | `str` / `SecretStr` |
| `APIFY_ACTOR_ID` | The specific Apify actor path/id to call for job scrapes. | `str` (defaults to `apify/linkedin-jobs-scraper`) |
| `REPLAY_FROM_CACHE` | If enabled, the system reads from local cached raw scrapes instead of hitting the live Apify API. | `bool` (defaults to `False`) |
| `REPLAY_CACHE_PATH` | Specific path to a cache file to load; if blank, the most recent scrape for the active source is used. | `str` / `None` |
| `LLM_API_KEY` | Secret access token for LLM providers (e.g., OpenAI/Anthropic/Google). Never logged or printed. | `str` / `SecretStr` |

### Path Settings

| Env Key | Default Value | Description |
| :--- | :--- | :--- |
| `DATABASE_PATH` | `data/app.db` | Relative or absolute path to the system of record SQLite database. |
| `RAW_SCRAPE_DIR` | `data/raw_scrapes` | Target folder where Apify scraper outputs are serialized prior to staging. |
| `LEDGER_XLSX_PATH` | `data/ledger.xlsx` | Output location for the Excel job tracking and audit spreadsheet. |
| `KNOWLEDGE_DIR` | `data/knowledge` | Directory containing localized context files and scoring criteria overrides. |
| `LOG_FILE_PATH` | `data/app.log` | Path to the rotating log file capturing structured system execution logs. |

### Scraper Params

| Env Key | Default Value | Description |
| :--- | :--- | :--- |
| `SCRAPER_QUERY` | `Software Engineer` | Job keyword terms to search. |
| `SCRAPER_LOCATION` | `Remote` | Geographical or format locator filter. |
| `SCRAPER_LIMIT` | `20` | Maximum number of records to retrieve per query session. |

### LLM Setup

| Env Key | Default Value | Description |
| :--- | :--- | :--- |
| `LLM_PROVIDER` | `openai` | String representing target API SDK engine (`openai`, `anthropic`, `google`). |
| `LLM_MODEL` | `gpt-4o` | Model identifier to target (e.g., `gpt-4o`, `claude-3-5-sonnet`). |
| `LLM_BASE_URL` | *None* | Optional custom base API URL endpoint for proxy routing. |

### Scoring

| Env Key | Default Value | Validation Constraints | Description |
| :--- | :--- | :--- | :--- |
| `SCORE_THRESHOLD` | `70` | `0 <= value <= 100` | Minimum score standard (0 to 100) below which candidates are auto-filtered out. |

### Cost & Rate Settings (ROUGH ESTIMATES)

| Env Key | Default Value | Description |
| :--- | :--- | :--- |
| `LLM_INPUT_TOKEN_RATE_USD` | `0.000005` | USD rate per LLM input token ($5.00 / 1M). |
| `LLM_OUTPUT_TOKEN_RATE_USD`| `0.000015` | USD rate per LLM output token ($15.00 / 1M). |
| `APIFY_CU_RATE_USD` | `0.25` | USD rate per Apify compute unit. |

### Runtime & Web Server

| Env Key | Default Value | Validation Constraints | Description |
| :--- | :--- | :--- | :--- |
| `LLM_CONCURRENCY` | `5` | `value >= 1` | Maximum parallel threads/requests dispatched to LLM. |
| `SQLITE_BUSY_TIMEOUT_MS`| `5000` | `value >= 0` | Milliseconds the SQLite client will wait when a database lock error occurs. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Target verbosity level of system execution logs. |
| `UI_HOST` | `127.0.0.1` | Valid host IP | Interface address NiceGUI/Streamlit UI server binds to. |
| `UI_PORT` | `8080` | Valid port | Port the web dashboard exposes. |

---

## Validation Details

1. **Fail-Fast Boot**: When `get_settings()` executes, all validators check values immediately. Missing or invalid formats will raise a `ConfigurationError` explaining which field caused the issue.
2. **Directory Isolation**: Before returning the config, the setup layer will verify that parent paths for DB or ledger documents are initialized on the filesystem automatically.
