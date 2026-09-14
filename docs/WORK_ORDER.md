# Work Order — Job Match Assistant

> **Status, 2026-09-14 — the original work order.** The body below is the Claude
> Project copy, unchanged. Progress is from `git log` and `docs/plans/`. These
> batch numbers are **not** the numbered steps in `docs/HANDOFF.md` §7; the two
> lists differ.
>
> - **1 — complete.** `58b8cb2` (C1, C2).
> - **2 — re-scoped, open.** Manifests repaired without pinning in `dc6749f`
>   (C3). `tests/conftest.py` (C5), version pinning and the `requires-python`
>   decision remain.
> - **3 — complete.** `1365977`, merged `c56ed01`.
> - **4 — open, partly done.** 4.1 struck, see `docs/plans/batch-03-single-ui.md`
>   — deleting the thread path made `_active_task` single-typed, so the
>   annotation became concrete and `Any` was never needed. 4.4 done in `1e163d7`
>   (B6). 4.2 (B2) and 4.5 (B1) remain.
> - **5 — complete, re-scoped** from analysis to a criteria rewrite: `52b4d7b`,
>   merged `cd240b4`. The analysis found the criteria measured the CV rather than
>   the posting.
> - **6 — open.** A2, A3, B10.
> - **7 — no plan or commit references it.**
> - **8 — open, parts absorbed.** C7 in `dc6749f`; an Apify-side per-run budget
>   cap in `16ae8b1`; a single-job scoring path, D6's minimal form, in `8df38a4`.
>   The full `score_jobs`, the LLM budget cap, B9, C8, A5 and the remaining
>   cleanups are open.
>
> Work done outside this order — the desktop launcher, tabbed dashboard, computed
> score, README rewrite, multi-query scraping, manual job entry, letter editing,
> PDF export and letter salutation — has one plan each in `docs/plans/`.

---

Batches are ordered by dependency, not by severity. Do not start a batch before
the one above it is merged and `py -m pytest -q` passes on `main`.

Audit IDs (A1, B7, C5 …) refer to `docs/HANDOFF.md`.

---

## Batch 1 — Establish a rollback point
**Branch:** none (this creates the repo) · **Audit:** C1, C2, C9

Nothing else happens before this is done.

1. `py scripts\backup.py --output-dir data\backups` — succeeds before anything is
   touched.
2. Add `secrets/` to `.gitignore`. Verify `.env`, `data/*.db`,
   `data/raw_scrapes/*`, `data/knowledge/*.md`, `data/backups/`, `secrets/` are
   all covered.
3. `git init`, then `git status --ignored` and read it end to end.
4. Only then `git add -A` and commit.

**Done when:** `git log` has one commit, and `git ls-files` contains no `.env`,
no `*.db`, no `service_account.json`.

**Risk:** if a secret lands in commit 1, rotate the Google service account key
and the LLM API key. Do not attempt history surgery on a one-commit repo —
delete `.git` and start over.

---

## Batch 2 — Make the suite verifiable
**Branch:** `chore/testable-clone` · **Audit:** C3, C5, C6

The suite currently reads the developer's real `.env`. Until this batch lands, no
refactor below can be verified by anyone, including on this machine.

1. `tests/conftest.py` with an autouse fixture that supplies synthetic `Settings`
   — a fake `LLM_API_KEY`, `SCRAPER_SOURCE`, tmp-path database, tmp-path
   knowledge dir — and clears `get_settings.cache_clear()` around each test.
2. `requirements.txt`: add `openai`, `nicegui`, `google-genai`. Pin every
   version.
3. `pyproject.toml`: add `google-genai`, remove `streamlit`, pin versions, set
   `asyncio_mode = "auto"`.
4. Decide the Python floor. Either raise `requires-python` to `>=3.14` or commit
   to supporting 3.11 — the latter means B7 is a real crash, not a latent one.

**Done when:** the suite passes with `.env` temporarily renamed. That is the
acceptance test — run it.

---

## Batch 3 — One UI
**Branch:** `refactor/single-ui` · **Audit:** A1, A4

Keep NiceGUI. Delete Streamlit.

1. Delete `src/main.py` (652 lines).
2. In `RunCoordinator.start_run()` (`src/coordinator/run_coordinator.py:57`),
   remove the daemon-thread + `asyncio.run` branch. Only the asyncio-task path
   remains.
3. Delete Streamlit-only tests and the `LEDGER_XLSX_PATH` diagnostics display
   that lived there.
4. Update `.agent/rules/architecture.md` to name NiceGUI.

**Why deletion rather than reconciliation:** A4 (one `asyncio.Lock` awaited from
multiple event loops) exists only because of the Streamlit path. Deleting it
removes the bug class rather than fixing it.

**Done when:** `grep -ri streamlit src/ tests/ pyproject.toml requirements.txt`
returns nothing, and the suite passes.

---

## Batch 4 — Correctness
**Branch:** `fix/correctness-batch` · **Audit:** B7, B2, B3, B6, B1

Small, independent commits. Each gets a failing-then-passing test.

| # | Fix | Location |
| --- | --- | --- |
| 4.1 | Import `Any` from `typing` | `src/coordinator/run_coordinator.py:55` |
| 4.2 | Pass `actor_call.stats.compute_units` from the scraper into `cost_accum.add_apify_usage` instead of the hardcoded `0.0` | `src/ingestion/` + `run_coordinator.py:109` |
| 4.3 | Reconcile the state-machine comment with the code. **The code is correct** — the UI depends on `matched -> applied`, `applied -> rejected`, `rejected -> matched`. Rewrite the comment. | `src/domain/models.py:20-33` |
| 4.4 | Add the untrusted-data instruction to `Writer.SYSTEM_PROMPT`, mirroring rule 3 of `Scorer.SYSTEM_PROMPT` | `src/skills/writer.py` |
| 4.5 | Add `first_seen_at` column, `user_version` 1 → 2. Stop `upsert_job` overwriting it on conflict. Point `list_ledger_rows` at the new column. | `src/persistence/service.py:76`, `src/persistence/database.py` |

**4.5 backfill rule, state it in the migration comment:** existing rows get
`first_seen_at = scraped_at`. The true first-seen date for the current rows has
already been walked forward by re-scrapes and is unrecoverable. The backfilled
value is a *floor*, not a fact.

> Note, 2026-09-14: the backfill direction stated above is inverted. `upsert_job`
> overwrites `scraped_at` on conflict, so the stored value is the *most recent*
> scrape and the true first-seen date is earlier. The backfilled value is a
> **ceiling**, not a floor. Correct this in the migration comment when 4.5 lands.

---

## Batch 5 — Calibrate the scorer
**Branch:** `chore/scorer-calibration` (analysis only, no production code) ·
**New — not in the audit**

96 of 151 jobs are `no_match`, `no_match` is terminal, and nothing has ever
measured whether `SCORE_THRESHOLD` is set correctly. The database contains 49
human-labelled rows: 5 `applied`, 44 `rejected`. Use them before designing any
scoring policy.

Protocol is in `ATS_DOMAIN_BRIEF.md` §4. Output is a written finding, not a
refactor.

**Done when:** you can state the score distribution for `applied` vs `rejected`
jobs and say whether the current threshold separates them. If it does not,
`ats_criteria.md` is the problem and batch 8's scoring policy must wait on a
criteria rewrite.

---

## Batch 6 — Coordinator seams
**Branch:** `refactor/coordinator-seams` · **Audit:** A2, A3, B10

1. Replace `from nicegui import ui; ui.notify(...)` in `sync_ledger()`
   (`run_coordinator.py:236`, `:243`) with an injected notification callback. The
   default is a no-op; the NiceGUI layer supplies the `ui.notify` implementation
   at wiring time.
2. Give `GoogleSheetLedger` its own `asyncio.Lock`. Stop passing
   `self.persistence_service._write_lock` (`run_coordinator.py:51`) — it reaches
   into a private attribute and lets a Sheets round-trip block every database
   write.
3. Make the `runs` table the single source of truth for run state.
   `get_active_run()` (`run_coordinator.py:190`) currently mutates state as a
   side effect of a getter and infers completion from task liveness. One read
   path, no inference.

---

## Batch 7 — Composition root
**Branch:** `refactor/composition-root` · **Audit:** A6

Deliberately after batches 4 and 6: this touches every module, and doing it first
would bury small correctness diffs in refactor noise.

One `build_app(settings)` constructs every service and injects `Settings`
explicitly. Remove `get_settings()` calls from `Normalizer.__init__`,
`database.connect`, `OpenAIAdapter`, `GoogleAdapter`, `CostAccumulator`, and the
three `build_*` factories.

**Done when:** `grep -rn "get_settings()" src/` matches only the composition root
and the config module. The `cache_clear()` workaround in the test fixtures can
then be deleted — that deletion is the proof.

---

## Batch 8 — Performance, scoring policy, cleanup
**Branch:** one per item · **Audit:** A5, B4, B5, B11, A7, A8, A9, B9, B12, C4,
C7, C8

**Performance (A5).** Do *not* build a connection pool — one user, 151 rows,
connection-per-call is not the bottleneck. The actual cost is
`list_jobs_with_match()` pulling every job's full `description` into memory on a
1-second timer. Fix in this order: (a) a list projection that excludes
`description`, (b) indexes on `jobs(status)` and `runs(started_at)`, (c) raise the
timer or make updates change-driven.

**Scoring policy (B4 + B5 — one decision, not two).** Build a single
`score_jobs(hashes, budget_cap)` entry point. The coordinator passes this run's
new hashes; the UI passes an explicit selection for re-scoring. Note that
re-scoring jobs left `scraped` by a failed run is the *recovery path*, not purely
a bug — the defect is that it is implicit and uncapped. Add a per-run budget cap
and a dry-run that reports projected cost without calling the LLM. This is what
stops `no_match` being a dead end for the jobs already there.

**Cleanup, lowest priority.** B11 (pick one definition of "written" — "has a
cover letter" is the more useful one, and label it in the UI). A7
(`skills/__init__.py` exports `Scorer` only). A8 (collapse `LinkedInJobsAdapter`
/ `IndeedJobsAdapter`; note the per-actor input mapping lives separately in
`ApifyScraperClient._build_run_input`, so a source is added in two places today).
A9 (`parse_dt` defined twice). B9 (delete `LEDGER_XLSX_PATH`, it is dead config).
B12 (chunk the `IN (...)` at 500 — theoretical at `SCRAPER_LIMIT=20`, five lines
when you are already in the file). C8
(`country=settings.scraper_country or "Portugal"` hardcodes a default in
orchestration). C4 (delete `terminal_output.txt`, `foundation-review.zip`,
`review.md`, the stray dataset JSON, `.pytest_cache/`, the stale `.pyc`). C7
(bring `CONFIG.md`, `README.md`, `.agent/rules/architecture.md` back in line).

---

## Deferred / open

- **Dedup false-negative rate is unmeasured.** Reposts with cosmetic title
  changes — "(m/f/d)", seniority suffixes, city vs. metro area — land as distinct
  `identity_hash` values today. Measure first: group the rows by normalized
  company + fuzzy title, count groups holding more than one hash. If the rate is
  material, add a *second* similarity key alongside `identity_hash`. Never
  redefine the first — it is the Sheets join key.
- **Backup is manual and all-or-nothing.** `scripts/backup.py` aborts if `.env`
  or `secrets/service_account.json` is missing, even when the database and
  knowledge files — the irreplaceable parts — are present. Make the secrets
  optional-with-warning and schedule it.