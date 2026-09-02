# Job Match Assistant — Architecture Handoff & Audit

**Audited:** 2026-09-02 · **State:** working tree at `c:\Users\USER\job-match-assistant` (no VCS)
**Purpose:** hand this repository to a Claude session tasked with improving its design and architecture.

---

## 1. What this system is

A local-first, single-user desktop web app that automates a job-application pipeline:

```
Apify scraper  ->  raw JSON cache  ->  normalize  ->  dedup  ->  SQLite  ->  LLM score (ATS)
                                                                        ->  LLM cover letter
                                                                        ->  Google Sheets mirror
```

The user opens a browser dashboard, clicks **Run Pipeline**, and gets a scored, deduplicated list of
job postings with generated cover letters and a status workflow (matched → written → applied / rejected).

**Current real usage** (from `data/app.db`): 151 jobs, 151 match results, 10 cover letters, 11 runs.
Status distribution: `no_match` 96, `rejected` 44, `matched` 6, `applied` 5. This is a working system,
not a prototype — changes must preserve the existing database.

---

## 2. Stack and entry points

| Concern | Choice |
| --- | --- |
| Language | Python 3.14 installed; `pyproject.toml` declares `requires-python = ">=3.11"` |
| Config | `pydantic-settings` + `.env`, fail-fast at boot |
| Web UI | **NiceGUI** (`src/ui/`) — the real entry point |
| Web UI (second) | **Streamlit** (`src/main.py`) — parallel, near-duplicate implementation |
| Persistence | SQLite (WAL), raw `sqlite3`, hand-rolled schema, `PRAGMA user_version` migrations |
| Scraping | Apify SDK (`apify-client`) + a replay-from-disk client |
| LLM | OpenAI SDK and Google GenAI SDK behind a `ProviderAdapter` protocol |
| Downstream | Google Sheets via `gspread` + service-account credentials |
| Logging | `structlog` → console + rotating JSON file, with a secret-redaction processor |
| Tests | pytest + pytest-asyncio, 157 tests, **all passing** (9.4 s) |

**Run the app:** `py src/ui/main.py` (NiceGUI, binds `UI_HOST:UI_PORT`, default `127.0.0.1:8080`).
**Run the other app:** `streamlit run src/main.py`.
**Run tests:** `py -m pytest -q` — *note: requires a valid `.env` in the repo root* (see C5).
**Backup:** `py scripts/backup.py [--output-dir data/backups] [--keep N]`.

---

## 3. Module map

```
src/
  config/         Settings (pydantic-settings), get_settings() lru_cache singleton, ConfigurationError
  domain/         Pure entities: JobPosting, JobIdentity, MatchResult, CoverLetter, Run, RunCost
                  + JobStatus/RunStatus enums + the status state machine (ALLOWED_TRANSITIONS)
  ingestion/      ScraperClient protocol; ApifyScraperClient (live) / ReplayScraperClient (offline);
                  RawScrapeCache (JSON envelopes on disk); Normalizer + per-source SourceAdapters;
                  DedupService; IngestionService (fetch + cache write)
  knowledge/      KnowledgeLoader — reads cv.md / persona.md / ats_criteria.md fresh from disk
  llm/            LLMRequest/Response/Usage types; ProviderAdapter protocol + registry;
                  OpenAIAdapter, GoogleAdapter; LLMClient (semaphore, timeout, retry/backoff, cost)
  skills/         Scorer (ATS JSON scoring), Writer (cover letter generation) — the two LLM "skills"
  persistence/    database.py (connect + init_db), PersistenceService (async facade over sqlite3),
                  GoogleSheetLedger (non-destructive Sheets sync)
  observability/  log_config.py (structlog wiring + redaction), metrics.py (CostAccumulator, RunReport)
  coordinator/    RunCoordinator — orchestrates the whole pipeline as a background task
  ui/             NiceGUI page + view-state builder (the live UI)
  main.py         Streamlit UI (the other one)
scripts/backup.py Operational backup: online SQLite snapshot + knowledge + .env + service account -> zip
tests/            157 tests, one file per module, fakes/stubs hand-rolled (no mock library)
```

Dependency direction is broadly clean: `domain` depends on nothing; `ingestion` / `persistence` /
`skills` depend on `domain` + `config` + `observability`; `coordinator` depends on everything; `ui`
depends on `coordinator` + `persistence`. Wiring is done by three factory functions —
`build_ingestion_service()`, `build_llm_client()`, `build_run_coordinator()` — each of which calls
`get_settings()` internally.

---

## 4. How the pipeline actually runs

`RunCoordinator.start_run()` ([run_coordinator.py:57](../src/coordinator/run_coordinator.py#L57))
builds a `ScrapeQuery` from settings, does a synchronous check-and-set on `_active_run`, then launches
`_execute_run` either as an asyncio task (if a loop is running — the NiceGUI case) or on a daemon
thread running its own `asyncio.run` (the Streamlit case).

`_execute_run` ([run_coordinator.py:94](../src/coordinator/run_coordinator.py#L94)):

1. `save_run(run)` with status `running`.
2. `IngestionService.fetch(query)` → raw records; writes a timestamped JSON envelope to
   `data/raw_scrapes/` unless replaying.
3. `Normalizer.normalize(records)` → `JobPosting[]` + a per-record error list. Malformed records are
   logged and skipped, never fatal.
4. `DedupService.filter_new()` → intra-batch dedup by `identity_hash`, then a single batched
   `existing_hashes` query against SQLite. Asserts `total == new + intra_dupes + already_seen`.
5. Upsert each new posting.
6. **Scoring:** loads the knowledge base, then `list_jobs(status=SCRAPED)` and scores **all** of them
   concurrently with `asyncio.gather` (bounded only by the `LLMClient` semaphore). Each job gets a
   `MatchResult`, a status transition to `matched` / `no_match` against `SCORE_THRESHOLD`, and a
   `save_run` write.
7. `sync_ledger()` — Google Sheets mirror; failures are logged and swallowed.
8. `finally`: finalize status, stamp `finished_at`, roll `CostAccumulator` up into `RunCost`, save.

**Identity & dedup.** `JobIdentity` normalizes company/title/location (NFKD accent-fold → lowercase →
strip non-alphanumerics → collapse whitespace), joins with `|`, and SHA-256s the result. That hash is
the primary key everywhere — jobs, match results, cover letters, and the Sheets ledger.

**Status state machine.** Defined once in `domain/models.py` and enforced in
`PersistenceService.set_status`, which reads the current status and calls `validate_transition` before
updating.

**Cover letters** are versioned (`UNIQUE(identity_hash, version)`), computed as `MAX(version) + 1`
inside the write lock. Nothing is ever overwritten.

**Google Sheets sync** is deliberately non-destructive: it reads the whole sheet, matches rows by
`identity_hash`, updates only changed cells in one `batch_update`, appends genuinely new rows, and
preserves any user-added columns outside `APP_OWNED_COLUMNS`. Missing app columns are appended as a
schema migration. The sheet is write-only from the app's perspective — never read back as state.

---

## 5. Data model

```sql
jobs(identity_hash PK, company, title, location, url, description, source NOT NULL,
     scraped_at NOT NULL, status NOT NULL CHECK IN (scraped|no_match|matched|written|applied|rejected))

match_results(identity_hash PK -> jobs ON DELETE CASCADE, score CHECK 0..100,
              dimensions TEXT /*json*/, reasons TEXT /*json*/, scored_at)

cover_letters(id PK AUTOINCREMENT, identity_hash -> jobs ON DELETE CASCADE, text,
              version CHECK >= 1, created_at, UNIQUE(identity_hash, version))

runs(run_id PK, source, status CHECK IN (queued|running|done|failed), started_at, finished_at,
     n_scraped, n_new, n_matched, n_no_match, n_errors,
     cost_total_input_tokens, cost_total_output_tokens, cost_total_llm_calls,
     cost_apify_compute_units, cost_apify_results, cost_estimated_usd)
```

`PRAGMA user_version = 1`. `init_db` creates everything if the version is 0 and, on every call, flips
any `running` / `queued` runs to `failed` (crash recovery). **No indexes exist** beyond the implicit
primary keys. All timestamps are ISO-8601 UTC strings; the domain layer rejects naive datetimes.

---

## 6. Audit findings

Ordered by how much they should influence a redesign. Nothing here is speculative — each item cites
the code.

### 6.1 Structural / architectural

**A1 — Two complete, divergent UIs.** `src/ui/page.py` (657 lines, NiceGUI) and `src/main.py`
(652 lines, Streamlit) both implement the dashboard, the cover-letter handler, the status transitions,
and their own CSS. They have already drifted: Streamlit has no actions on pending/unscored jobs and a
different definition of "Strong Matches". `.agent/rules/architecture.md` still declares Streamlit as
the stack; the app that ships is NiceGUI. **Pick one and delete the other** — this is the single
largest source of duplicated logic in the repo.

**A2 — The coordinator imports the UI framework.** `sync_ledger()` does
`from nicegui import ui; ui.notify(...)` on both the success and the failure path
([run_coordinator.py:236](../src/coordinator/run_coordinator.py#L236),
[:243](../src/coordinator/run_coordinator.py#L243)). Orchestration knows about the presentation
framework, which makes `sync_ledger` untestable outside NiceGUI and unusable from the Streamlit UI or
a CLI. It needs an event/callback seam.

**A3 — Ledger sync reuses the persistence write lock.** `RunCoordinator.__init__` passes
`self.persistence_service._write_lock`
([run_coordinator.py:51](../src/coordinator/run_coordinator.py#L51)) as the `GoogleSheetLedger` lock.
Two problems: it reaches into a private attribute, and it means a slow Google Sheets round-trip blocks
*every database write* in the process for its duration.

**A4 — Cross-event-loop lock hazard.** `PersistenceService` creates one `asyncio.Lock` per instance.
Under Streamlit, `run_async()` calls `asyncio.run()` per interaction (a new loop each time) while
`start_run()` spawns a thread with *its own* loop — the same lock object is then awaited from
different event loops. Under NiceGUI (single loop) this is fine. A reason to resolve A1 by deletion
rather than by fixing both paths.

**A5 — No connection pooling, and a 1-second full refresh.** Every `PersistenceService` method opens a
fresh `sqlite3.Connection`, re-issues three PRAGMAs, and closes it. `list_jobs_with_match()` runs on a
1-second NiceGUI timer and pulls every job's full `description` text into memory on each tick — three
queries and three connections per second per connected browser.

**A6 — Config is a global singleton reached from deep inside modules.** `get_settings()` is an
`lru_cache`d function called directly by `Normalizer.__init__`, `database.connect`, `OpenAIAdapter`,
`GoogleAdapter`, `CostAccumulator`, and all three factories. Tests work around it with
`get_settings.cache_clear()` in autouse fixtures. Explicit injection at a composition root would
remove both the workaround and the hidden coupling.

**A7 — `skills/` is a thin, inconsistent package.** `__init__.py` exports `Scorer` only; `Writer` is
imported everywhere as `from skills.writer import Writer`. Both classes duplicate the same
format-prompt → call-LLM → validate → log shape.

**A8 — Source-adapter duplication.** `LinkedInJobsAdapter` and `IndeedJobsAdapter`
([normalization.py](../src/ingestion/normalization.py)) are identical except for `source_name`. A third
source is registered by aliasing a dict entry:
`ADAPTER_REGISTRY["agentx_all_jobs"] = ADAPTER_REGISTRY["apify_linkedin"]`. The per-actor *input*
mapping lives separately in `ApifyScraperClient._build_run_input`, so adding a source means editing two
unrelated places.

**A9 — `parse_dt` is defined twice**, identically, in `ingestion/cache.py` and `persistence/service.py`.

### 6.2 Correctness and behaviour

**B1 — `upsert_job` overwrites `scraped_at` on conflict**
([service.py:76](../src/persistence/service.py#L76)). The Sheets ledger publishes that same column as
`first_seen_at` (`list_ledger_rows`). So "first seen" silently moves forward every time a posting is
re-scraped. Needs a separate `first_seen_at` column.

**B2 — Apify cost is always zero.** `_execute_run` calls
`cost_accum.add_apify_usage(compute_units=0.0, ...)`
([run_coordinator.py:109](../src/coordinator/run_coordinator.py#L109)). `ApifyScraperClient.fetch`
reads `actor_call.stats.compute_units` but only *logs* it — the value never reaches the accumulator.
Every `runs.cost_estimated_usd` under-reports by the entire Apify component, and `APIFY_CU_RATE_USD`
is effectively dead config.

**B3 — The state machine contradicts its own specification comment.** The comment block at
[models.py:20-28](../src/domain/models.py#L20) says `matched -> {written, rejected}` and that `applied`
and `rejected` are terminal. The code allows `matched -> applied`, `applied -> rejected`, and
`rejected -> matched` ([models.py:30-33](../src/domain/models.py#L30)). The code is what the UI relies
on (Mark Applied, Undo Reject). The comment is stale and misleading — one of them must change.

**B4 — Re-scoring is unbounded and unscoped.** Step 6 scores every job in the DB with status `scraped`,
not just this run's new jobs ([run_coordinator.py:131](../src/coordinator/run_coordinator.py#L131)).
Any job left `scraped` by a prior failed run is re-scored — and paid for — on every subsequent run.
There is no cap, no budget check, and no dry-run.

**B5 — `no_match` is terminal, so scoring is irreversible.** Lowering `SCORE_THRESHOLD` or editing
`ats_criteria.md` cannot revive the 96 jobs already marked `no_match`; there is no re-score path in the
UI or the coordinator.

**B6 — The prompt-injection guard is asymmetric.** `Scorer.SYSTEM_PROMPT` explicitly instructs the
model to treat the job posting as untrusted data (rule 3). `Writer.SYSTEM_PROMPT` interpolates the same
attacker-controlled `job.description` with an anti-fabrication rule but **no** untrusted-data
instruction — and the writer is the component whose output the user pastes into a real application.

**B7 — Latent `NameError` on Python 3.11–3.13.**
`self._active_task: Optional[Any] = None`
([run_coordinator.py:55](../src/coordinator/run_coordinator.py#L55)) — `Any` is never imported. It runs
today only because Python 3.14's lazy annotations (PEP 649) never evaluate it. On the 3.11 that
`pyproject.toml` advertises, constructing a `RunCoordinator` raises.

**B8 — The retry loop holds the semaphore across backoff sleeps.** `LLMClient.complete` acquires the
semaphore and then loops with `await asyncio.sleep(backoff)` inside it
([client.py](../src/llm/client.py)). A rate-limited request occupies one of the `LLM_CONCURRENCY` slots
while doing nothing, so throughput collapses exactly when the API is throttling.

**B9 — `LEDGER_XLSX_PATH` is dead config.** No xlsx writer exists anywhere in `src/`. The setting is
validated, its parent directory is created at boot, and it is displayed in the Streamlit diagnostics
panel — but nothing ever writes it. The ledger is Google Sheets.

**B10 — `get_active_run()` mutates state as a side effect of a getter**, inferring completion from
thread liveness / task doneness
([run_coordinator.py:190](../src/coordinator/run_coordinator.py#L190)). Run state lives in three places
at once: the in-memory `_active_run`, the `runs` table, and the task handle.

**B11 — Two definitions of "written".** `Counters.written` counts
`DISTINCT identity_hash FROM cover_letters`; the status breakdown counts jobs whose status is `written`.
A job that was written and then applied is counted by one and not the other, and the UI displays both
numbers side by side.

**B12 — `existing_hashes` builds an unbounded `IN (...)`.** Fine at `SCRAPER_LIMIT=20`; it becomes a
SQLite parameter-limit failure at scale. Needs chunking or a temp table.

### 6.3 Repository hygiene and operations

**C1 — The project is not under version control.** No `.git` anywhere. There is no history, no diff, no
rollback, and no way to review a change. **Fix this first** — the `.gitignore` is already written and
almost correct.

**C2 — `secrets/` is not gitignored.** `.gitignore` covers `.env`, `data/*.db`, `data/raw_scrapes/*`,
the live `data/knowledge/*.md`, and `data/backups/`, but **not** `secrets/service_account.json`, which
is present on disk with real credentials. Add `secrets/` before the first commit.

**C3 — Dependency manifests disagree with reality.** `requirements.txt` omits `openai`, `nicegui`, and
`google-genai` — all of which the code imports. `pyproject.toml` omits `google-genai` and still lists
`streamlit`. Neither pins versions. Installing from either manifest yields a non-working app.

**C4 — Repo clutter at root:** `terminal_output.txt` (378 KB), `foundation-review.zip` (44 KB),
`review.md` (36 KB — a raw source dump, not a review), `dataset_all-jobs-scraper_*.json` (133 KB), plus
a `.pytest_cache/` directory and a stale `src/observability/__pycache__/logging.cpython-314.pyc` from a
module that no longer exists.

**C5 — Tests depend on the developer's real `.env`.** There is no `tests/conftest.py`; fixtures call
`get_settings.cache_clear()` and let `Settings` read the real `.env` from the CWD. A fresh clone has no
`.env`, so `SCRAPER_SOURCE` and `LLM_API_KEY` are missing and the suite fails at import time. The 157
passing tests are passing against personal configuration.

**C6 — No CI, no linter, no formatter, no type checker, no coverage gate.** `pyproject.toml` contains
only `pythonpath` under `[tool.pytest.ini_options]`; `asyncio_mode` is not set either.

**C7 — Documentation drift.** `CONFIG.md` omits `LLM_MAX_RETRIES`, `LLM_TIMEOUT_SECONDS`,
`SCRAPER_COUNTRY`, `SCRAPER_POSTED_SINCE`, `GOOGLE_SERVICE_ACCOUNT_PATH`, and `LEDGER_SHEET_ID` — all of
which `Settings` defines and the app uses. `README.md` documents only the backup script. `docs/adrs/`
is empty. `.agent/rules/architecture.md` names the wrong UI framework.

**C8 — A hardcoded default leaks into orchestration:** `country=settings.scraper_country or "Portugal"`
([run_coordinator.py:68](../src/coordinator/run_coordinator.py#L68)).

**C9 — Backup is all-or-nothing.** `scripts/backup.py` aborts the entire backup if `.env` *or*
`secrets/service_account.json` is missing, even when the database and knowledge files — the
irreplaceable parts — are present. It is also manual-only; nothing schedules it.

### 6.4 What is genuinely good — preserve these

- **The domain layer is clean.** Pure Pydantic entities, no I/O, timezone-aware validation, a single
  authoritative state machine, and a well-specified identity/hash normalization. Build on it.
- **Cost and offline discipline.** `RawScrapeCache` + `ReplayScraperClient` + `REPLAY_FROM_CACHE` make
  the entire pipeline runnable end to end with zero Apify spend. 13 real scrape envelopes are on disk.
- **Secret hygiene in logs.** The structlog redaction processor redacts by key name, by suffix, and by
  substring-matching the actual API key value; `Scorer` and `Writer` log token counts and hashes only,
  never prompt or completion text.
- **The Sheets sync is well designed.** Non-destructive, cell-diffed, batched, user-column-preserving,
  additive schema migration, one-way by policy.
- **Error taxonomy in the LLM layer.** `TransientLLMError` vs `PermanentLLMError`, mapped per-provider
  from real SDK exception types, with retries only on the transient branch.
- **Test coverage is broad and hand-rolled** — 157 tests across every module, using explicit fakes
  rather than mock magic. A real safety net for refactoring, once C5 is fixed.

---

## 7. Suggested order of work for the next session

1. **`git init`, add `secrets/` to `.gitignore`, commit the current state.** Nothing else should happen
   before there is a rollback point. *(C1, C2)*
2. **Fix the manifests and add `tests/conftest.py`** so `pytest` runs on a clean clone against
   synthetic settings. *(C3, C5)* — this is what makes every later refactor verifiable.
3. **Choose one UI and delete the other.** Recommend keeping NiceGUI: it is the real entry point, it has
   the async model the coordinator assumes, and dropping Streamlit removes A4 entirely. *(A1)*
4. **Introduce a composition root.** One `build_app(settings)` that constructs every service and injects
   `Settings` explicitly; remove `get_settings()` calls from `Normalizer`, `connect`, the LLM adapters,
   and `CostAccumulator`. *(A6)*
5. **Fix the leaks around the coordinator:** give it an event/notification callback instead of
   `ui.notify`, give `GoogleSheetLedger` its own lock, and move run-state ownership into the `runs`
   table behind a single read path. *(A2, A3, B10)*
6. **Correctness batch:** add a `first_seen_at` column with a migration to `user_version = 2`; wire
   Apify compute units into the accumulator; import `Any`; reconcile the state-machine comment with the
   code; add the untrusted-data rule to the writer prompt. *(B1, B2, B7, B3, B6)*
7. **Persistence performance:** a shared connection or a small pool, indexes on `jobs(status)` and
   `runs(started_at)`, a list-view projection that excludes `description`, and change-driven UI updates
   instead of a 1-second full refresh. *(A5)*
8. **Scoring policy:** scope re-scoring to the current run's new jobs, add a per-run budget cap, and add
   an explicit re-score action so `no_match` stops being a dead end. *(B4, B5)*
9. **Then** the cleanups: collapse the duplicate source adapters, deduplicate `parse_dt`, remove
   `LEDGER_XLSX_PATH`, delete the root clutter, and bring `CONFIG.md`, `README.md`, and
   `.agent/rules/architecture.md` back in line with the code. *(A8, A9, B9, C4, C7)*

**Invariants any redesign must hold:** the SQLite file is the system of record and the existing 151 rows
must survive; `identity_hash` semantics must not change (it is the join key into the live Google Sheet);
Google Sheets stays write-only; and raw scrapes must keep landing on disk before normalization so
offline replay keeps working.
