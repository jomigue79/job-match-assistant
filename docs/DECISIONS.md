# Decision Log — Job Match Assistant

> **Status, 2026-09-14.** D1–D9 are the Claude Project copy, unchanged. D10 is
> filed here from its draft in `docs/plans/launcher.md`, where it was written on
> merge and never filed. Figures and line numbers in each entry are as of its
> filing date — `page.py` is recorded at 657 lines, for instance; it is 658.
>
> - **Cited by plans or code:** D1 (batch 3), D6 (bounded re-scoring and
>   `score_jobs(hashes, budget_cap)`, partly realised in `16ae8b1` and
>   `8df38a4`), D7 (`identity_hash` frozen, cited at `src/domain/models.py:88`),
>   D8 and D9 (`docs/plans/batch-05-criteria-rewrite.md`).
> - **D2–D5 are not cited anywhere in the repository.**
> - **Plan-local labels are not these decisions.** `edit-cover-letter.md`,
>   `letter-pdf-export.md` and `letter-salutation-signoff.md` each number their
>   own approval choices D1–D5. Those labels refer only to the plan they appear
>   in, not to entries here.
> - Decisions made after D10 are recorded in the relevant plan under
>   `docs/plans/`, not filed here.

---

A decision here is **settled**: the Project should not re-litigate it in later
sessions. To reverse one, add a new entry that supersedes it — do not edit
history.

Format per entry: decision, date, why, what it costs, what would reverse it.

---

## D1 — NiceGUI is the only UI. Streamlit is deleted.
**2026-09-02**

Two complete dashboards existed (`src/ui/page.py`, 657 lines; `src/main.py`, 652
lines) and had already drifted — Streamlit lacked actions on unscored jobs and
used a different definition of "Strong Matches". NiceGUI is the real entry point
and matches the coordinator's async model.

**Cost:** loses Streamlit's faster iteration for throwaway diagnostics panels.
**Bonus:** removes the cross-event-loop `asyncio.Lock` hazard (A4) entirely,
rather than fixing it in two places.
**Would reverse it:** nothing foreseeable. A CLI would be added as a third entry
point, not as a replacement UI.

## D2 — The state machine code is authoritative; the comment is stale.
**2026-09-02**

`src/domain/models.py:20-28` documents `matched -> {written, rejected}` with
`applied` and `rejected` terminal. The code at `:30-33` allows
`matched -> applied`, `applied -> rejected`, `rejected -> matched`. The UI's
"Mark Applied" and "Undo Reject" depend on the code.

**Decision:** rewrite the comment to match the code. Do not restrict the
transitions.
**Would reverse it:** a deliberate product decision that reject is final.

## D3 — `first_seen_at` is backfilled as a floor, not a fact.
**2026-09-02**

`upsert_job` overwrites `scraped_at` on conflict
(`src/persistence/service.py:76`) while the Sheets ledger publishes that column
as `first_seen_at`. Adding a real column at `user_version = 2` fixes it going
forward, but the true first-seen date for the existing rows has already been
walked forward by re-scrapes and cannot be recovered.

**Decision:** backfill `first_seen_at = scraped_at` and state in the migration
comment that pre-migration values are a floor.
**Would reverse it:** nothing — the data is gone. Raw scrape envelopes in
`data/raw_scrapes/` could in principle be replayed to reconstruct earlier dates;
judged not worth the effort.

> Note, 2026-09-14: the direction is inverted. Because `upsert_job` keeps the
> *most recent* scrape, the true first-seen date is earlier than the stored
> value, so the backfill is a **ceiling**, not a floor.

## D4 — No connection pool.
**2026-09-02**

The audit groups "no pooling" with "1-second full refresh" under A5. They are not
the same problem. One user, 151 rows, local SQLite in WAL mode:
connection-per-call is not the bottleneck. The real cost is
`list_jobs_with_match()` loading every job's full `description` on a 1-second
timer.

**Decision:** fix the projection and add indexes on `jobs(status)` and
`runs(started_at)`. Do not build a pool.
**Would reverse it:** measured contention, or a second concurrent consumer of the
database.

## D5 — Composition root comes after the correctness fixes.
**2026-09-02**

A6 (global `get_settings()` reached from inside `Normalizer`, `connect`, both LLM
adapters, `CostAccumulator`) is legitimate and worth doing. It is also the
highest-churn change in the backlog. Landing it before the small correctness
fixes buries one-line diffs in refactor noise and makes review impossible.

**Decision:** batches 4 and 6 first, composition root as batch 7.
**Would reverse it:** a correctness fix that turns out to be impossible without
injection.

## D6 — Scoring scope and re-scoring are one design, not two features.
**2026-09-02**

B4 (unbounded re-scoring of every `scraped` job) and B5 (`no_match` is a dead
end) pull in opposite directions if implemented separately. Note also that
re-scoring jobs left `scraped` by a failed run is the recovery path, not purely a
defect — the problem is that it is implicit and uncapped.

**Decision:** one `score_jobs(hashes, budget_cap)` entry point, with a dry-run
that reports projected cost. Coordinator passes this run's new hashes; the UI
passes explicit selections.
**Would reverse it:** nothing; this is strictly more capable than either half
alone.

## D7 — `identity_hash` is frozen.
**2026-09-02**

It is the join key into a live Google Sheet and the primary key across `jobs`,
`match_results`, and `cover_letters`. Redefining the normalization orphans every
existing row on both sides.

**Decision:** dedup quality improvements, if any, arrive as a *second* similarity
key alongside the hash. The hash input and algorithm do not change.
**Would reverse it:** abandoning the Google Sheet and accepting a full re-scrape.

## D8 — Scoring is a fit judgment, not an ATS simulation.
**2026-09-02**

Real applicant tracking systems screen by boolean keyword search and knockout
questions, not semantic scoring. Designing criteria that emulate a machine that
does not work that way produces a score nobody can interpret — and is the likely
cause of 96 of 151 jobs landing in a terminal `no_match`.

**Decision:** hard requirements become binary gates evaluated first; personal fit
becomes the weighted score. See `ATS_DOMAIN_BRIEF.md` §2. The skill keeps its
filename for now; renaming is cosmetic and can wait.
**Would reverse it:** calibration (batch 5) showing the current single-score
design already separates `applied` from `rejected` cleanly.

## D9 — Threshold changes require calibration data.
**2026-09-02**

`SCORE_THRESHOLD` has never been evaluated against outcomes, despite 49
human-labelled rows sitting in the database.

**Decision:** no change to `SCORE_THRESHOLD` or `ats_criteria.md` without the
calibration finding from work-order batch 5. State the small-n and
label-contamination caveats in any such finding.
**Would reverse it:** nothing; this is a standing rule.

## D10 — Closing the browser tab shuts the app down; an in-flight run is cancelled and recorded as failed.
**2026-09-02**

`ui.run()` defaulted to `reload=True`, spawning a uvicorn reloader child, so the
app ran as two processes and killing one orphaned the other
(`src/ui/main.py:27`). No lifecycle hook existed anywhere in `src/`. And
`_execute_run`'s `except Exception` (`run_coordinator.py:145`) does not catch
`CancelledError`, so a cancelled run recorded itself as `done`.

**Decision:** `reload=False`; `app.on_disconnect` with a grace period triggers
shutdown; `RunCoordinator.shutdown()` cancels the task and writes `failed` itself
rather than trusting `_execute_run`'s `finally`.
**Cost:** loses auto-reload during development. Restart to pick up changes. Exit
is delayed when a synchronous Apify scrape is in flight, because `to_thread` work
cannot be cancelled.
**Would reverse it:** switching to NiceGUI native mode, which changes the
window-ownership model entirely.

---

## Template for new entries

```markdown
## Dn — <one-line decision in the imperative>
**YYYY-MM-DD**

<Two to four sentences: what was observed, cited by path and line, and what was chosen.>

**Cost:** <what this gives up>
**Would reverse it:** <the specific evidence that would change the decision>
```