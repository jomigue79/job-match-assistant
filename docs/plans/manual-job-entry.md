# Implementation Plan — Manual Job Entry

**Branch:** `feat/manual-entry` · **Base:** `master` @ `6c40dd3`
**Not in the work order.** User-requested. Backlog item from
`docs/plans/writer-guard-and-letter-modal.md`.

## Intent

Every job in the database arrived through the scraper. Postings found on
LinkedIn, through word of mouth, or on a company's own careers page cannot enter
the tool at all. The user's stated problem is not having enough postings to
apply to; multi-query widened the funnel, and this covers what no query reaches.

## Design

A form in the UI. Company, title, location, URL, description — all free text.
Construct a `JobPosting` with `source="manual"`, upsert it, score it
immediately, and it appears as a normal card.

Everything downstream is unchanged. `JobPosting` computes its own
`identity_hash` from company + title + location (`models.py:169-179`). In
principle a manually-entered job dedups against a scraped one with the same
three fields. **In practice that mostly does not hold.** Scraped locations are
stored as stringified dicts that include coordinates, so a hand-typed "Porto"
normalizes and hashes differently, and pasting a posting the scraper already has
will usually create a duplicate. This is the known location-in-hash defect. It
is out of scope here — documented, not fixed — and is in the Backlog.

`jobs.source` is `NOT NULL` with no CHECK constraint (verified against the live
schema), so `"manual"` needs no migration. Stored values today are job-board
names — Indeed, LinkedIn, SAP, Talent.com, Jooble — so `manual` sits
consistently alongside them.

`upsert_job` preserves `status` on conflict (`service.py:72-76`), so re-pasting a
job already marked applied updates its description without resetting it. The
same conflict clause **overwrites `source`, `url`, `description` and
`scraped_at`**. Re-pasting a job the scraper found therefore changes its `source`
to `manual`, and that is correct: the user did just enter it manually.

## Immediate scoring, and why the threshold does not apply

**Decided: the job is scored on submit, not deferred to the next run.**

Deferring is nearly free — `_execute_run`'s step 6 scores every job with status
`scraped` — but a job pasted because the user wants to know about *that* job,
sitting unscored until the next Run Pipeline, defeats the point.

`Scorer.score` is currently reachable only through
`RunCoordinator._score_and_persist` (`run_coordinator.py:230-261`), which
requires a `Run` for its counters. This batch adds:

**`RunCoordinator.score_one(identity_hash, respect_threshold=True) -> MatchResult`**
— loads the knowledge base, fetches the job, scores it, saves the `MatchResult`,
and transitions its status. No `Run` row, no run counters. Raises on failure
rather than swallowing, because the UI shows the error to the user directly.

- **Shared logic, not duplicated.** The score → threshold → save → transition
  sequence moves into a helper, `_score_and_transition`, called by both
  `_score_and_persist` and `score_one`. The run-only parts — the actioned-state
  guard, counters, `save_run`, and swallowing `ScorerError`/`LLMError` into
  `n_errors` — stay in `_score_and_persist`.
- **Knowledge is loaded on every call**, as the run does at
  `run_coordinator.py:186`. A missing, empty or unreadable file raises
  `KnowledgeLoadError`, which propagates to the UI. Placeholders only log a
  warning, so a job is scored against unfilled files without any UI signal.
- **Cost goes to a log line only.** `score_one` passes its own
  `CostAccumulator` and logs the tokens and estimated cost. There is no `Run` row
  to attribute it to, so it appears nowhere in the app or database. This matches
  the writer, whose cost is already not recorded.

**Refused while a run is active.** `score_one` raises `RunAlreadyActiveError` if a
run is `RUNNING`, and the UI checks the same before writing anything. The race it
prevents: a manual job upserted as `scraped` just before the run's step 6 lists
`scraped` jobs is scored by both paths. Both call `set_status(scraped → matched)`;
the second raises `InvalidStateTransitionError`, and because `_score_and_persist`
catches only `ScorerError`/`LLMError`, the exception fails the run. The window is
a few seconds on a live run and about a second in replay mode. The dialog keeps
the entered text, so waiting costs nothing.

**A job not in `scraped` is not re-scored.** It returns its stored `MatchResult`
with no LLM call and no transition. This is required, not optional: every status
except `scraped` rejects a transition to `matched` — including `matched` itself
(`ALLOWED_TRANSITIONS[MATCHED]` does not contain `MATCHED`), so re-pasting a
matched job would otherwise raise. Re-scoring without a transition was
considered and **rejected: it costs money and overwrites the score an existing
letter or application was based on.** The trade-off is that re-pasting a richer
description does not refresh the score, and a `no_match` job re-pasted on purpose
stays in Non-Matches, since `no_match` is terminal.

**When `respect_threshold` is `False` — the manual-entry path — the job
transitions to `matched` regardless of score.** A manually-entered job has
already passed the only filter that matters: the user chose to paste it.
`SCORE_THRESHOLD` exists to filter what the scraper surfaced. And `no_match` is
terminal (`ALLOWED_TRANSITIONS[NO_MATCH] = set()`, `models.py:35`), so routing a
deliberately-pasted job there would strand it in the Non-Matches tab as a compact
row with no Write Letter, no Mark Applied and no way back. The score is still
computed, stored and displayed — as information, not as a gate.

**The exemption follows the job, not the code path.** `_score_and_persist` passes
`respect_threshold=(job.source != "manual")`. Without it, a manual job whose
scoring failed on submit stays `scraped`, and the next Run Pipeline scores it
against the threshold and sends it to terminal `no_match` — the exact failure
this batch exists to prevent. Scraped jobs are unaffected.

This is the minimal form of D6's `score_jobs(hashes, budget_cap)`. The budget cap
is not needed here: one job, one call, roughly $0.0023.

## Scope — in

1. **`src/coordinator/run_coordinator.py`** — `score_one(identity_hash,
   respect_threshold=True)` as above; the shared `_score_and_transition` helper;
   `_score_and_persist` calls the helper with
   `respect_threshold=(job.source != "manual")`.

2. **`src/ui/page.py`**
   - An **Add Job** button in the header row, beside Run Pipeline and Sync
     Ledger (`page.py:411-418`).
   - A `ui.dialog` form: Company, Title, Location, URL (all single-line),
     Description (textarea). Company and Title required; the rest optional.
   - A module-level `add_manual_job_handler`, testable without a UI like
     `generate_cover_letter_handler`: validate, refuse during an active run,
     construct the `JobPosting`, upsert, call
     `score_one(hash, respect_threshold=False)`, return the score and status.
     The dialog notifies with the result, closes, and refreshes.
   - Created per click and cleared on close. Built inside a stable
     `dialog_host` element that `rebuild_cards` never clears — see below.
   - Errors notify and leave the dialog open with the entered text intact. A user
     who has pasted a long description must not lose it to a failed call.

3. **`src/ui/page.py` — `show_letter_dialog` fix, found while implementing the
   Add Job dialog.** `dialog.py:30` attaches the dialog to the client layout, but
   `dialog.py:33-37` creates a canary element *in the current context* and
   deletes the dialog when the canary is collected. A click handler runs in the
   sender's parent slot (`events.py:461`). The View button sits inside a card
   that `rebuild_cards` builds, so the canary lands in a container
   `rebuild_cards` clears, and an open letter dialog can be deleted by a rebuild.
   Its docstring claimed the opposite. Both dialogs are now built inside
   `dialog_host`, and the docstring is corrected.

4. **`tests/test_manual_entry.py`** — new tests; see Acceptance criteria.

5. **`data/knowledge/ats_criteria.md`** — no change. A manually-entered job is
   scored by the same criteria, including the gates.

## Validation

- Company and Title must be non-empty. Without them `identity_hash` is computed
  from empty strings and every such job collides on the same hash. The check is
  on the normalized value, so punctuation-only input is refused too.
- Description should be non-empty in practice — the scorer has nothing to judge
  otherwise — but is not required. A job with no description will score low on
  every dimension, which is honest.
- The description is **attacker-controlled in the same way a scraped one is**. It
  goes through the same `<job_posting_untrusted>` delimiters in both the scorer
  and writer prompts. No new injection surface, but no exemption either: a user
  pasting from a hostile posting is exactly the case the guard exists for.

## Scope — explicitly out

- Editing a job after creation. Paste again; `upsert_job` updates the description
  and preserves status.
- Deleting a manually-entered job. Reject it like any other.
- URL validation. Free text; a malformed URL renders as a dead link on the card.
- Bulk paste or import.
- `identity_hash` semantics, the location-in-hash defect, `is_remote`/`work_mode`.
- Any schema change. `source` accepts `manual` as-is.
- D6's full `score_jobs(hashes, budget_cap)` with dry-run. `score_one` is the
  single-job case only.
- The `n_errors` conflation and the token-rate defaults from the multi-query
  backlog.
- Changing `SCORE_THRESHOLD` or the meaning of `no_match` for scraped jobs.
  `respect_threshold=False` applies to the manual path only.

## Acceptance criteria

1. `py -m pytest -q` passes. No existing test modified.
2. New tests cover: `score_one` scores and transitions a `scraped` job;
   `score_one` on a non-existent hash raises; a `JobPosting` with
   `source="manual"` upserts and reads back; a manual job and a scraped job with
   identical company/title/location share an `identity_hash`. Also: a non-scraped
   job returns its stored score without an LLM call; `score_one` refuses during an
   active run; knowledge and scorer failures propagate and leave the job
   `scraped`; a run exempts manual jobs from the threshold; the handler refuses
   missing fields and active runs without writing anything.
3. Manual: Add Job opens a dialog; submitting a real posting produces a scored
   card in the Pipeline tab within a few seconds.
4. Manual: the score and dimensions are visible on the card, and Write Letter
   works on it.
5. Manual: submitting with an empty Company or Title is refused with a message,
   and the dialog stays open with the text intact.
6. `select source, count(*) from jobs group by source` shows `manual`.
7. A manually-entered job scoring below `SCORE_THRESHOLD` appears in the Pipeline
   tab as a full card with Write Letter, Mark Applied and Reject available. A new
   test asserts `score_one(..., respect_threshold=False)` sets `matched` on a low
   score.

## Backlog

- **Manual entry does not dedup against scraped jobs.** Scraped locations are
  stored as stringified dicts with coordinates, so a hand-typed location hashes
  differently and pasting a posting the scraper already has usually creates a
  duplicate. This is the location-in-hash defect; fixing identity is its own
  change.

## Rollback

`git checkout master`, delete the branch. No schema change. Manually-entered jobs
remain in the database and render normally — they are ordinary rows.
