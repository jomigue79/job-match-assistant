# Implementation Plan — Multi-Query Scraping

**Branch:** `feat/multi-query` · **Base:** `master` @ `3ffe781`
**Not in the work order.** User-requested. Backlog item from
`docs/plans/writer-guard-and-letter-modal.md`.

## Intent

A run searches one job title in one location. `RunCoordinator.start_run()`
(`run_coordinator.py:62-70`) builds a single `ScrapeQuery` from
`SCRAPER_QUERY` and `SCRAPER_LOCATION`. The real ceiling on intake is that
single search, not `SCRAPER_LIMIT` — the actor returned 39 records against a
limit of 10, so volume is not the constraint; coverage is.

The user's stated problem is not having enough postings to apply to.

## Design

**`SCRAPER_QUERY` becomes a comma-separated list of job titles.**
`SCRAPER_LOCATION` stays a single value. One actor call per title, results
pooled before normalization, dedup run once across the pooled batch.

**Rejected: a cross-product of titles and locations.** Three titles × three
cities is nine Actor Starts per run. Multiple titles against one location covers
most of what the user would apply to, at a third of the cost. A remote-only
sweep, if wanted later, is a separate run rather than a multiplier.

**Rejected: batching into one actor call.** The actor's `keyword` input is a
single required string (its README), so N titles means N calls. Cost scales
linearly and the budget cap below exists because of that.

## Budget cap

Each query is a separate **Actor Start at $0.01** plus results at roughly
**$0.003 each**. LLM scoring adds roughly **$0.003 per new job**. Run
`25861b43`, which scored 4 jobs, recorded $0.118 — but that figure was computed
with the default token rates, which are GPT-4o prices ($5 / $15 per million
tokens). At Gemini 2.5 Flash paid-tier rates ($0.30 / $2.50 per million) the
same tokens (19,557 input, 1,368 output) cost about $0.0093, roughly
**$0.0023 per job** — close to the $0.003 estimate. The plan's cost model was
right; the database's recorded cost was wrong.

- **`SCRAPER_MAX_QUERIES`**, default 5, hard ceiling. More titles than this in
  `SCRAPER_QUERY` is a configuration error raised at boot, not silently
  truncated.
- **`RUN_BUDGET_CAP_USD`**, default 2.00. Before the scrape begins, the
  coordinator computes a projected cost from the query count and
  `SCRAPER_LIMIT`, logs it, and **refuses the run** if it exceeds the cap. The
  run is recorded as `failed` with the projection in the log.
- **`SCRAPER_RESULTS_PER_LIMIT`**, default 4.5. The actor applies
  `SCRAPER_LIMIT` per platform, so a query returns more records than its limit:
  run `a7640ad9` returned 39 records at limit 10. A projection on `SCRAPER_LIMIT`
  alone came in about 3x low. The projection is
  `sum(actor_start + limit * results_per_limit * result_rate)` over the queries.
  The default is calibrated from nine envelopes (2026-09, all at limit 10):
  eight single-query envelopes returning 35-40 records, and run `a73b878e`'s
  two-query envelope whose queries returned 44 and 45. That is a multiplier
  range of 3.5-4.5; the default is set to the observed maximum so the
  projection does not under-report. It is actor-specific.
- In replay mode the projection is $0.00: replay makes no Actor Start.
- The projection covers Apify only. LLM cost depends on how many postings turn
  out to be new, which is unknowable before dedup. State that in the log line
  rather than pretending otherwise.

This is D6's `budget_cap` arriving early, in its simplest useful form.

## Caching — the one design decision that matters

`RawScrapeCache.write` (`cache.py:24-50`) takes one `ScrapeQuery` and writes one
envelope. `ReplayScraperClient.fetch` (`replay_client.py:29`) calls
`read_latest(source)` and returns that one file's records.

**One merged envelope per run.** All queries' records go into a single file, and
the envelope gains a `queries` list naming every query whose records it holds.
An envelope holding exactly one query also keeps the legacy `query` object.
Replay then returns the whole run's records unchanged.

**Replay is fetched once.** `ReplayScraperClient` ignores its query and returns
the whole newest envelope, so calling it once per query would return the merged
run N times. It declares `replays_whole_run = True`, and `IngestionService.fetch`
calls such a client once regardless of the query count.

Rejected: one envelope per query. `read_latest` returns only the newest file, so
replay would silently give back one query's results out of N — degrading the
offline path that invariant 4 exists to protect.

**Backward compatible.** `read` (`cache.py:100-123`) validates only `records`,
`source` and `fetched_at`; `list_cached` (`:68-73`) the same. The `query` key is
never read by anything. The 21 existing envelopes continue to work.

## Scope — in

1. **`src/config/settings.py`**
   - `scraper_query` documented as comma-separated; add a validator splitting on
     commas, stripping whitespace, dropping empties, rejecting duplicate titles
     (case-insensitive), and rejecting a list longer than `scraper_max_queries`.
   - New: `scraper_max_queries: int = 5` (`SCRAPER_MAX_QUERIES`).
   - New: `run_budget_cap_usd: float = 2.00` (`RUN_BUDGET_CAP_USD`).
   - New: `apify_actor_start_usd: float = 0.01` (`APIFY_ACTOR_START_USD`) and
     `apify_result_usd: float = 0.003` (`APIFY_RESULT_USD`) — the projection
     needs rates, and hardcoding them in the coordinator is the C8 defect.
   - New: `scraper_results_per_limit: float = 4.5`
     (`SCRAPER_RESULTS_PER_LIMIT`) — see Budget cap.

2. **`src/coordinator/run_coordinator.py`**
   - `start_run` builds a **list** of `ScrapeQuery`, one per title, sharing
     location, limit, country and posted_since. An empty list raises
     `ValueError` before the active-run guard and before any `Run` is
     constructed; nothing that can raise sits between `self._active_run = run`
     and `create_task` (the window fixed in `1365977`).
   - Inside `_execute_run`, after the run is persisted and before the fetch,
     compute the projected Apify cost and compare to the cap. Over the cap, the
     run is recorded failed with the projection logged, through the existing
     `finally`.
   - `_execute_run` takes `List[ScrapeQuery]` and makes **one**
     `IngestionService.fetch` call (`run_coordinator.py:108`) passing the whole
     list. The per-query loop lives in the service. Pooled records are then
     normalized and deduped once.
   - A failed query does not fail the run: the service raises
     `PartialFetchError` carrying the other queries' records, and the
     coordinator counts the failures in `n_errors` and continues. One dead
     source should not lose the others. If every query fails, the run fails as
     it does today.

3. **`src/ingestion/service.py`** — `fetch` accepts a list, loops over it
   calling the client once per query, pools the records, and writes one merged
   envelope. Keep the single-query signature working (a single query is a list
   of one) so nothing else has to change. A single failing query re-raises its
   original error unchanged.

4. **`src/ingestion/cache.py`** — `write` accepts a list of queries; envelope
   grows a `queries` array. Reading is unchanged.

5. **`.env.example` and `CONFIG.md`** — the five new keys, and
   `SCRAPER_QUERY` redocumented as a list.

6. **`src/ingestion/types.py`** — new `PartialFetchError` carrying `records`
   and `failures`.

7. **`src/ingestion/__init__.py`** — export `PartialFetchError`.

8. **`src/ingestion/replay_client.py`** — `replays_whole_run = True` class
   attribute, so the service calls the replay client once.

9. **`README.md:50`** — `SCRAPER_QUERY` described as a comma-separated list of
   titles rather than a single title.

## Scope — explicitly out

- Multiple locations. Single `SCRAPER_LOCATION`, by design.
- `SCRAPER_LIMIT` semantics. The actor applies it per platform; not our concern
  here.
- Manual job entry. Separate backlog item, separate branch.
- `identity_hash`, the location-inside-hash defect, `is_remote`/`work_mode`.
- The per-run LLM budget cap and dry-run from D6's full design. The Apify-side
  projection here is the minimum that makes multi-query safe to run.
- `SCORE_THRESHOLD`. Unchanged at 60; the calibration question needs more data,
  not a config change.

## Acceptance criteria

1. `py -m pytest -q` passes. Existing ingestion and coordinator tests must not
   be modified — report failures before changing anything.
2. `SCRAPER_QUERY="Project Manager,Technical Project Manager"` produces two
   Actor Starts in one run, and the log names each query.
3. One envelope is written per run, containing a `queries` array with both
   entries and the pooled records.
4. `REPLAY_FROM_CACHE=True` against that envelope returns all pooled records.
5. An existing pre-change envelope still replays without error.
6. `SCRAPER_QUERY` with more than `SCRAPER_MAX_QUERIES` entries fails at boot
   with a clear message.
7. A projected cost above `RUN_BUDGET_CAP_USD` refuses the run and logs the
   projection.
8. A single failing query leaves the run alive: the others are scraped, scored,
   and `n_errors` reflects the failure.
9. Dedup collapses a posting found by two different queries into one job.

## Backlog

- **A budget refusal is invisible in the UI at the moment it happens.** The
  refusal happens inside `_execute_run`, so `page.py`'s `trigger_run` still
  shows "Background pipeline run triggered successfully" while the run is
  recorded `failed`. The UI has no channel for a refusal reason; A2's
  notification callback (batch 6) is the seam that would fix it.
- **The results-per-limit multiplier is calibrated from nine envelopes**
  (2026-09, all at limit 10, 35-45 records per query, a multiplier range of
  3.5-4.5, default set to the observed maximum) and is actor-specific. If the
  projection drifts from actual spend, this is the number to adjust.
- **`n_errors` conflates normalization skips with real failures.** Run
  `a73b878e` recorded 20 errors, all of them postings with no company field —
  11 unique records, most counted twice because both queries returned them. The
  run succeeded. A count that reads as failure when nothing failed is
  unreadable; normalization skips need their own counter.
- **`cost_estimated_usd` is only as good as the configured token rates**, and
  nothing validates them against the configured model. A provider swap silently
  invalidates every recorded cost.

## Rollback

`git checkout master`, delete the branch. No schema change, no migration.
Existing cache envelopes remain readable either way.