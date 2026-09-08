# Implementation Plan — Compute the Score in Code

**Branch:** `fix/computed-score` · **Base:** `master` @ `2f7ebbf`
**Audit:** new. Follows batch 5's first backlog item, promoted by live evidence.

## Evidence

First live run under the rewritten criteria, 2026-09-08, run
`a7640ad9-6027-484f-903a-fc351f415caa`: 39 scraped, 31 new, 5 matched, 26
no_match, 3 normalization errors, $0.91.

The criteria file §0 states the formula literally and instructs the model to
recompute if the arithmetic does not hold. **8 of 10 sampled rows failed it:**

| Job | Weighted sum | Model's score | Error |
| --- | --- | --- | --- |
| TeamViewer, Project Engineer | 57 | 45 | −12 |
| ALTEN, Data engineer | 50 | 35 | −15 |
| iTRTech, Project Manager | 41 | 25 | −16 |
| FedEx, Project Engineer | 42 | 50 | +8 |
| Controlar, Automation Project | 34 | 38 | +4 |
| Aspire, Project Manager | 30 | 29 | −1 |
| Quinta Marques Gomes | 27 | 28 | +1 |
| Lumya Living | 26 | 29 | +3 |

The two passing rows were gated jobs where every dimension is 0 — arithmetically
trivial. Errors run in both directions, so the model is not applying a consistent
alternative rule; it is producing a holistic score and filling in dimensions
separately.

`no_match` is terminal. A 16-point downward error on a job that should have
scored 62 is a job the user never sees, permanently.

## What worked, and stays

- **Gates fire correctly.** The language gate — which did not exist before batch 5
  — caught an Italian-fluency requirement on its first run. A location gate fired
  on a Lisbon role.
- **Flags are live.** "FLAG: No salary range stated" appeared as specified.
- **No truncation** at `max_tokens=1000` with five reasoned dimensions plus flags.
- **No mojibake.** Zero replacement characters across all 86 match results.
- **Scores now spread** across 0–50 with per-posting variation, against the old
  file's constant 30s.

## Decision

**The model returns dimensions only. `Scorer` computes the score.**

Rejected: validating the model's arithmetic and retrying. Asking an LLM for a
weighted sum and then checking its homework is strictly worse than doing the sum
in code. The model is demonstrably bad at it and the arithmetic is trivial.

**Architect's dissent, recorded:** this moves scoring policy from
`data/knowledge/` into code, against the design's principle that policy lives in
editable knowledge files. **HR expert's answer:** the weights are not the policy.
The dimensions and the gates are judgment and they stay in the file. A weighted
sum is arithmetic.

**Cost:** reweighting becomes a code change plus a test run, rather than a
markdown edit.

## Scope — in

1. **`src/skills/scorer.py`**
   - Add a module-level `DIMENSION_WEIGHTS` mapping the five exact dimension
     names to their weights: Technical role content 0.30, Requirements coverage
     0.25, AI literacy & development 0.15, Seniority & scope 0.15, Domain &
     context 0.15. Assert at import that they sum to 1.0.
   - Remove `"score"` from `SYSTEM_PROMPT`'s JSON schema (`:26-36`). The model no
     longer returns it.
   - Replace the score validation at `:104-110` with validation of the dimension
     set: exactly the five expected keys, no extras, no omissions, each value
     numeric and within 0–100.
   - Compute `score = round(sum(weight * value))` and pass it to `MatchResult`.
   - On any dimension-set failure, raise `ScorerError` naming what was wrong.

2. **`data/knowledge/ats_criteria.md`**
   - §0 no longer instructs the model to compute a weighted sum. It states the
     weights as documentation of what the code applies, and says the score is
     computed downstream.
   - §6's output schema drops the `score` key.
   - The gate rule changes shape: a failed gate means **every dimension is 0**,
     which the code then computes to a score of 0. Same outcome, stated in terms
     of what the model now controls.

3. **`data/knowledge/ats_criteria.md.example`** — mirror both changes.

## Failure behaviour — confirmed with the user

`ScorerError` is raised after the LLM call returns, so it is **not** caught by
`LLMClient`'s retry loop, which handles `TransientLLMError` and
`PermanentLLMError` only. It propagates to `_score_and_persist`'s
`except (ScorerError, LLMError)` at `run_coordinator.py:186`, which logs,
increments `run.n_errors`, and returns without saving a `MatchResult` or
transitioning status.

**The job stays `scraped`** and appears in the Pipeline tab as Pending. The next
run re-scores it, because step 6 scores every `scraped` job — B4's unbounded
re-scoring acting as the recovery path D6 describes.

No new retry logic. A posting that fails validation repeatedly will sit visibly
in Pending, which is a useful signal that something about it breaks the scorer.

**Known cost:** each re-score attempt on a persistently failing job costs an LLM
call. At one job this is noise. At many it is a bill, and B4's per-run budget cap
(batch 8) is the answer.

## Scope — explicitly out

- The existing 86 match results. The 26 new `no_match` rows were scored with
  broken arithmetic and are terminal; the user has accepted losing them.
  No re-score path is built here — that is D6 / batch 8.
- `SCORE_THRESHOLD`. Unchanged at 60. Whether it is right under computed scores
  is a question for after this lands, with real data.
- `settings.py:47`'s default of 70 against `.env`'s 60. Real defect, batch 4.
- `max_tokens` (`scorer.py:81`). Held at 1000; no truncation observed.
- `src/skills/writer.py`, including B6's missing untrusted-data instruction.
- Multi-query scraping. `_build_run_input` takes one `ScrapeQuery`; widening the
  search beyond "Project Manager" in Porto is a feature, not config. Backlog.
- `SCRAPER_LIMIT`. Held at 10 until computed scores are verified on a live run.
  Note the actor returned 39 records against a limit of 10, so the limit does not
  bound intake as expected — worth understanding before raising it.

## Acceptance criteria

1. `py -m pytest -q` passes. `tests/test_scorer.py` asserts on the current
   validation path; existing tests that feed a `score` field will need updating.
   **Report any failure before changing a test** — some may be revealing
   something.
2. New tests cover: correct computation from known dimensions; a missing
   dimension key raising `ScorerError`; an extra key raising `ScorerError`; a
   value above 100 raising `ScorerError`; all-zero dimensions computing to 0.
3. `Select-String -Path .\src\skills\scorer.py -Pattern "DIMENSION_WEIGHTS"`
   matches, and the weights sum to 1.0.
4. `data/knowledge/ats_criteria.md` contains no instruction to the model to
   compute or return a score.
5. On a live run, every new `match_results` row satisfies
   `score == round(sum(weight * dimension))` by construction. Verify with the
   same query used to find the defect.

## Rollback

`git checkout master`, delete the branch. `data/knowledge/ats_criteria.md` is
untracked — back it up to `.md.bak2` before editing (`*.bak` is gitignored;
confirm `.bak2` is too, or use `.bak` with a different stem).