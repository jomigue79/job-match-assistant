# Implementation Plan — Not-Applicable Dimensions

**Branch:** `fix/dimension-not-applicable` · **Base:** `master` @ `af951dc`
**Not in the work order.** User-requested.

## Intent

A posting that never mentions AI, automation or development work scores 0 on the
AI literacy dimension. That is correct as a judgement — the dimension measures
the posting, and the property is absent — but the 15% weight still drags 15
points out of the total. A job perfect on the other four dimensions caps at 85.

On 2026-09-11, 23 of the 28 jobs scored had AI literacy at 0 and 5 did not.
Across the 90 rows scored on the current criteria, 70 are 0 and 20 are not (the
other 55 rows predate the criteria rewrite and use different dimension names).
Those 70 are depressed by up to 15 points, and `SCORE_THRESHOLD = 60` has been
filtering them against numbers that cannot reach 100.

> Correction, 2026-09-14: this section originally said "Every job scored on
> 2026-09-11 had AI literacy at 0", and that every score in the database was
> depressed. Both were false; the figures above are from `match_results`.

## Design

**The model returns `null` for a dimension that does not apply. The code
redistributes its weight across the dimensions that do.**

Worked example, the Ankix posting, which never mentions AI:

```
current:
  Technical role content     80  x 0.30 = 24.0
  Requirements coverage      75  x 0.25 = 18.75
  AI literacy & development   0  x 0.15 =  0.0
  Seniority & scope          80  x 0.15 = 12.0
  Domain & context           70  x 0.15 = 10.5
                                          65.25 -> 65

with redistribution:
  applicable weights: 0.30 + 0.25 + 0.15 + 0.15 = 0.85

  Technical role content     80  x (0.30/0.85) = 28.2
  Requirements coverage      75  x (0.25/0.85) = 22.1
  Seniority & scope          80  x (0.15/0.85) = 14.1
  Domain & context           70  x (0.15/0.85) = 12.4
                                                 76.8 -> 77
```

Same judgements, same dimensions, 65 becomes 77. A job where every dimension
applies is unaffected — there is nothing to redistribute.

**Why `null` rather than a sentinel.** `-1` stays numeric but a sentinel leaks
into a chart or an average six months later. Omitting the key was rejected
because a missing key currently means the model did not do the work, and that
guard is worth keeping. `null` is what JSON means by "no value".

**Only AI literacy & development may be null.** The other four always apply: a
posting always has role content, stated or absent requirements, some seniority
signal, and a context. If the model returns `null` for any of them, that is an
error, not a judgement.

**If every dimension were null** the applicable weight would be zero and the
score undefined. Unreachable given the rule above, but it raises rather than
divides by zero.

## Scope — in

1. **`src/skills/scorer.py`**
   - `NULLABLE_DIMENSIONS`, containing `AI literacy & development` only.
   - Validation: a value may be `null` **only** for a dimension in that set.
     `null` elsewhere raises, naming the dimension. Numeric values keep the
     existing 0–100 range check.
   - Score computation: sum the weights of the non-null dimensions, divide each
     by that total, apply. Raise if the applicable weight is zero.
   - The five-key requirement is unchanged. All five keys must be present; one
     of them may be null.
   - `SYSTEM_PROMPT`'s JSON schema line becomes "numeric rating 0-100, or null
     only where the evaluation criteria allow it". A system prompt saying
     "numeric rating 0-100" while the criteria permit null is a higher-authority
     channel contradicting a lower one — the same shape as the `persona.md`
     failure, where a user-prompt cue beat a system-prompt language rule.
   - The breakdown keeps `None` rather than coercing with `float(v)`, which
     would raise `TypeError` — not a `ScorerError`, so it would escape
     `_score_and_persist` and fail the whole run.

2. **`data/knowledge/ats_criteria.md`** — §0, §3.3 and §6.
   - §3.3 currently says a posting that never mentions AI scores **0**. It
     becomes: return `null`, and say so in `reasons`. The band table keeps its
     `0` row, redefined: `0` means the role does involve AI, automation or
     development work and the CV evidences none of it; `null` means the posting
     does not involve it at all.
     > Correction, 2026-09-14: this plan originally dropped the `0` row, which
     > left no band for a role that involves AI where the CV shows none.
   - §6's output schema shows `null` as a permitted value for that one key.
   - §0 explains that the code redistributes a null dimension's weight across
     the rest, so the model understands that `null` is not a penalty.
   - §0's gate rule: a gated posting sets every dimension to 0, including AI
     literacy — never `null` — so "every dimension 0" keeps meaning a failed
     gate.

3. **`data/knowledge/ats_criteria.md.example`** — mirror both.

4. **`src/ui/page.py`** — the card's dimension list (`page.py:780-784`)
   iterates `dimension_breakdown` and would print `None`. A null dimension
   renders as "n/a" and is visibly distinct from a genuine 0. The rendering is a
   module-level `format_dimension_value` helper so it can be tested without
   starting NiceGUI.

5. **`src/domain/models.py:198`** — `dimension_breakdown: Dict[str, float]`
   becomes `Dict[str, Optional[float]]`. `models.py` is on
   PROJECT_INSTRUCTIONS' do-not-refactor list; this is a one-line type
   widening, not a refactor. The entities, validators, state machine and
   identity hash are untouched. Without it, `MatchResult` rejects `None`, and a
   single null row would break every dashboard refresh (`list_jobs_with_match`)
   and letter generation for that job (`get_match_result`). No schema change:
   `dimensions` is a JSON string, and `null` round-trips through `json.dumps`
   and `json.loads`.

## Existing rows

`match_results.dimensions` holds a number for AI literacy on every row scored
on the current criteria so far — `0.0` on 70 of the 90. Those are not migrated:
the score that was stored is the score the job was judged on, and rewriting
history to a number nobody saw would be worse than a low number.

New scores use the new arithmetic. The two populations are not comparable, which
matters for the threshold question and is stated in the Backlog.

## Scope — explicitly out

- Re-scoring existing jobs. No migration, no schema change.
- `SCORE_THRESHOLD`. It stays at 60. Whether it is right under redistributed
  scores needs data this batch does not produce.
- The weights themselves. 30/25/15/15/15 is unchanged.
- Making any other dimension nullable.
- `persona.md` and the writer. Separate batch.
- `n_errors`, the location-in-hash defect, `tests/conftest.py`,
  `settings.py`'s threshold default.

## Acceptance criteria

1. `py -m pytest -q` passes. No existing test modified.
2. New tests cover: a null AI dimension redistributes and the score matches a
   hand-calculated literal; no nulls gives the same score as today; null on a
   non-nullable dimension raises naming it; all five null raises; a null
   dimension is stored as null, not as 0.
3. `Select-String -Path .\data\knowledge\ats_criteria.md -Pattern "null"`
   matches in §0, §3.3 and §6.
4. Manual: score a posting that does not mention AI and confirm the stored
   dimensions contain `null`, the card shows "n/a", and the score is higher than
   the same dimensions would have produced before.
5. Manual: a posting that does mention AI still scores all five.

## Backlog

- Scores from before this change are not comparable with scores after it. Any
  future calibration of `SCORE_THRESHOLD` must either use only post-change rows
  or recompute the old ones from their stored dimensions — which is possible,
  since `dimensions` is stored, but was judged not worth doing here.
- If AI literacy returns null on most postings, its 15% is rarely applied and
  the weight may belong elsewhere. Worth revisiting after thirty or forty scores.
- **The null rule is not reliably followed.** Of 14 postings scored live, 2
  of the 3 that returned a number should have returned null by their own
  stored reasons. SMCP's reason says the role involves no AI and it returned
  0; Siemens' reason says the posting does not mention AI and it returned 40
  inferred from company context, which §3.3 forbids — storing 58 where null
  gives 61, above the threshold. This is the same class of problem as the
  weighted sum the model used to get wrong, but unlike arithmetic it cannot
  move into code: whether a posting involves AI is a judgement. Worth
  revisiting if the miss rate stays near 1 in 7.
- **Requirements coverage ignored §3.2 on one posting.** SMCP scored 0 while
  its own reason says the posting states no explicit must-haves; §3.2 says
  score 50 in that case. Unrelated to this batch, found while verifying it.

## Rollback

`git checkout master`, delete the branch. `data/knowledge/ats_criteria.md` is
gitignored — it was backed up before editing to
`data/knowledge/ats_criteria_pre_nullable.md.bak` (also gitignored); copy it
back. Rows scored under this change keep their
null dimensions; nothing else reads them.