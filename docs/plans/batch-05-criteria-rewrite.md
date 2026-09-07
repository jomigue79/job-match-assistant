# Implementation Plan — Batch 5: Criteria Rewrite

**Branch:** `chore/criteria-rewrite` · **Base:** `master` @ `c56ed01`
**Audit:** B5, D8, D9 · **Supersedes** work-order batch 5, which specified analysis only.

## Calibration finding — the analysis that authorises this

Work-order batch 5 asked whether `SCORE_THRESHOLD` separates the user's own
`applied` decisions from `rejected`. It does not, and no threshold value would.

Stored scores, 151 rows, all four labels:

    applied   n=5    65, 78, 78, 85, 85
    matched   n=5    75, 75, 75, 75, 88
    rejected  n=45   65×14, 68×3, 75×12, 78×6, 85×7, 88×3
    no_match  n=96   0×52, then 15–55

Every score value appearing in `applied` also appears in `rejected`, more often.
At 65: one applied, fourteen rejected. At 85: two applied, seven rejected. Means
are 78.2 against 74.2 with n=5 positives — indistinguishable from noise.

Per-dimension medians, `applied` against `rejected`:

| Dimension | applied | rejected |
| --- | --- | --- |
| Project Governance & Methodology | 30 | 30 |
| Technical Translation & Workflows | 30 | 30 |
| AI Literacy & Emerging Technologies | 5 | 10 |
| Multi-Stakeholder Management | 10 | 10 |

`ATS_DOMAIN_BRIEF.md` §4 step 5: a dimension whose distribution does not differ
by label contributes noise and its weight should go to zero. All four qualify.
AI Literacy scores *higher* on rejected jobs. Project Governance returns exactly
30 on every job above the gate, in every label.

**Root cause.** Read what the dimensions evaluate: "the presence of structured
delivery frameworks", "**the candidate's capability** to bridge the gap",
"application of automation and technical tools". All four measure the CV. The CV
is the same document on all 151 jobs, so 70% of the weight (A + B) is a constant.
The stored `reasons` confirm it — every one is a statement about the candidate,
none about whether the job is worth applying to.

The score is not badly calibrated. It measures the wrong variable.

**Caveats, per §4.** n=49 labelled with 5 positives is directional, not
statistical. The labels are contaminated: `no_match` is terminal, so every
labelled row sits above the current threshold by construction and the
false-positive half of a threshold sweep is structurally zero. And `cv.md` was
rewritten on 2026-09-07 (6855 → 5188 bytes), so the CV that produced these scores
no longer exists. None of this weakens the finding — the defect is structural, not
a matter of where the line sits.

**Consequence.** `SCORE_THRESHOLD` is not changed by this batch. D9 required
calibration data before touching it; the data says the criteria file is the
defect. §4's closing rule applies: rewrite `ats_criteria.md`, do not move the
threshold.

## Additional defects in the current `ats_criteria.md`

- **No language gate.** §1 has only Work Authorization and Geographic Alignment.
  The candidate holds Portuguese (native) and English (C1) only. Postings
  requiring any other working language pass the gates untouched.
  `ATS_DOMAIN_BRIEF.md` §2 lists language as a hard gate; it was never written.
- **Corrupted equivalence markers.** Lines 17–18 read `PM² ![][image1] PMP`
  where an equivalence symbol belongs — a Google Docs export artefact — with the
  base64 PNG defined at line 59. The whole file, blob included, is interpolated
  into every scoring prompt at `src/skills/scorer.py:70`.
- **§3 "Mandatory Keyword Thresholds" is ATS simulation**, settled against by D8.
- **§5 terminology is inverted:** "Minimum Ceiling: 3 years", "Maximum Baseline".
- **Dimension scale is undefined.** Weights sum to 100 but nothing states whether
  a dimension returns 0–weight or 0–100. Stored data shows both: an `applied` row
  sums 30+30+0+8 = 68 against a stated score of 78, while `no_match` rows carry
  dimension values of 80 with total scores of 55. `Scorer` validates only that
  values are numeric (`scorer.py:118-122`).

## Candidate constraints — decided with the user, 2026-09-07

Source of truth for the gates. `persona.md` is a *writing* persona and contains no
job preferences; these were previously invented rather than derived, contrary to
`ATS_DOMAIN_BRIEF.md` §2.

- Working languages: Portuguese and English only.
- Location: Porto, Braga or Aveiro for on-site/hybrid. **Lisbon on-site is a
  refusal.** Fully remote acceptable within Portugal or EU timezones.
- Refused outright: unpaid, commission-only, internship.
- Accepted: freelance, recibos verdes, staffing-agency placement.
- Experience: **four years** of project management (Ground Control Studios
  2021–2025, plus Yclient advisory 2026–present). The prior CV's "10+ years"
  claim is gone from `cv.md` as of 2026-09-07.
- Seniority: knockout **only** on an explicit stated minimum above four years.
  "Senior" with no number scores low, does not gate.
- Salary: **flag only**, never a gate.
- Titles: Project Manager. Product Owner postings are scored on content, not
  gated on the title.
- Domain: open.

## Scope — in

1. **Rewrite `data/knowledge/ats_criteria.md` entirely.** New structure below.
2. Nothing else. This batch changes one file, and that file is gitignored.

### Hard gates — binary, evaluated first

One failure means `no_match` with the failing gate named in `reasons`. No soft
scoring is performed. A gate that cannot be evaluated from the posting text does
**not** fire — absence of evidence is not a failure (`ATS_DOMAIN_BRIEF.md` §2).

1. **Language** — a working language other than Portuguese or English is stated
   as *required*. "Nice to have" does not fire the gate.
2. **Location** — on-site or hybrid outside Porto, Braga or Aveiro. Fully remote
   passes regardless of company location, within Portugal or EU timezones.
3. **Work authorization** — sponsorship required for a non-EU territory.
4. **Seniority floor** — an explicit stated minimum above four years.
5. **Contract** — unpaid, commission-only, or internship.

### Soft dimensions — each scored 0–100 independently, weight applied after

The scale is stated explicitly because its absence produced the incoherent totals
above. The final score is the weighted sum and must be arithmetically derivable
from the dimensions.

| Dimension | Weight | What it asks **about the posting** |
| --- | --- | --- |
| Technical role content | 30 | Software delivery ownership, engineering teams, technical requirements, SDLC |
| Requirements coverage | 25 | Proportion of stated must-haves the CV evidences |
| AI literacy & development | 15 | Does the role involve AI, automation or development work, and is it evidenced |
| Seniority & scope | 15 | Team size, ownership, reporting line against four years and teams to 15 |
| Domain & context | 15 | Sector, company type, contract, working language, remote policy |

Every dimension carries a written reason. Unreasoned scores are not auditable and
cannot be calibrated.

**AI literacy & development measures the posting, not the CV.** The prior version
asked whether the candidate has AI skills — the same answer on all 151 jobs, hence
the flat distribution. A posting that never mentions AI scores 0 here, correctly:
that is a property of the job, not a deficiency in the candidate.

**Never score on protected characteristics** — age, nationality, gender, family
status, health, religion, political affiliation, union membership. A stated
preference on any of these is a red flag about the employer, surfaced in
`reasons`, never an input to the score.

### Flags — reported in `reasons`, never in the numeric score

- No salary range where the local market or law expects one
- "Fast-paced", "wear many hats", "rockstar" clustered together
- More years required in a technology than the technology has existed
- Unpaid or commission-only framing for a described full-time role
- Preferences stated on protected characteristics
- Largely boilerplate with no description of actual work

## Scope — explicitly out

- `SCORE_THRESHOLD`. Unchanged at 60. See the finding above.
- `src/skills/scorer.py` validation logic. No validation of dimension keys,
  ranges, or the weighted-sum relationship is added here — that is a code
  change and belongs in its own batch. Stated as backlog below.
  **Prompt text is in scope**, added during execution: the criteria file no
  longer simulates an ATS, so leaving `SYSTEM_PROMPT` casting the model as
  "an expert Applicant Tracking System" and `USER_PROMPT_TEMPLATE` labelling
  the block "ATS CRITERIA:" would have shipped a prompt contradicting its own
  payload on the first live run. Four lines changed, no logic touched.
- Re-scoring the existing 151 jobs. The user will scrape fresh. New postings get
  the new criteria automatically, so `score_jobs(hashes, budget_cap)` (D6) and the
  `no_match → scraped` transition are not prerequisites.
- `persona.md`. It is the writing persona and is correct for that purpose.
- `cv.md`. Rewritten by the user 2026-09-07, outside this project.
- `src/skills/writer.py`, including the missing untrusted-data instruction
  (B6) — work-order batch 4.4.

## Acceptance criteria

1. `Select-String -Path .\data\knowledge\ats_criteria.md -Pattern "image1|base64"`
   returns nothing.
2. The file states the 0–100-per-dimension scale explicitly, and states that the
   final score is the weighted sum.
3. All five gates are present, language among them.
4. Weights sum to 100.
5. `py -m pytest -q` passes. The file is knowledge, not code, so this should be
   unaffected; run it to confirm nothing read the old structure.
6. `py -c "import sys; sys.path.insert(0,'src'); from knowledge import KnowledgeLoader; from config import get_settings; k=KnowledgeLoader(get_settings().knowledge_dir).load(); print(len(k.ats_criteria))"`
   loads without error and reports a plausible length.
7. The file is clean UTF-8 with zero replacement characters.

## Note on version control

`data/knowledge/*.md` is gitignored — deliberately, it holds personal data. So
this rewrite has no history and the `.example` companion is what a reader of the
repo sees. Update `data/knowledge/ats_criteria.md.example` in the same change so
the tracked template reflects the new structure.

## What this commit does not contain

`data/knowledge/ats_criteria.md` — the rewrite itself — is gitignored and is
not in this commit. What is committed: the tracked `.example` template
mirroring the new structure, the `.gitignore` rule covering `*.bak` in that
directory, the scorer prompt reframing, and this plan. The live criteria file
exists only in the working tree and in `data/backups/`.

## Backlog, not this batch

- **Mojibake in stored output.** `match_results.reasons` contains `PM�` and
  `Resid�ncia`. All three knowledge files are clean UTF-8 with zero replacement
  characters, so the corruption is introduced during the LLM round trip or the
  SQLite write. The same path carries Portuguese job descriptions into the
  **writer**, whose output is pasted into real applications. New audit item;
  higher priority than most of batch 8.
- **`Scorer` does not validate dimension structure.** It accepts any numeric
  value under any key (`scorer.py:118-122`). With a fixed dimension set and a
  stated scale, it should validate the key set, the 0–100 range, and that the
  weighted sum matches `score` within a tolerance.
- **No `applied_at` column.** An applied-jobs view cannot sort by recency or show
  what needs chasing. Folds into batch 4.5, which is already adding
  `first_seen_at` at `user_version` 2.
- **Scores are not traceable to an input.** `cv.md` is gitignored and has no
  history, so a score change cannot be attributed to the CV or to the criteria.
  Recording a hash of each knowledge file on `match_results` would fix it.
  Schema change.
- **`max_tokens=1000` (`scorer.py:81`) may be tight** for five reasoned
  dimensions plus flags. Truncation fails loudly as `ScorerError` at
  `scorer.py:99` and leaves the job `scraped`, so it will be visible on the first
  live run. Do not pre-emptively raise it.
- **The pre-rewrite `ats_criteria.md.example` had five dimensions of which four
  measured the candidate rather than the posting** — Hard requirements match,
  Skills/keyword overlap, Title & seniority alignment, Domain/industry relevance,
  Nice-to-haves. The tracked template taught the same defect the calibration
  finding identified in the live file.

## Rollback

`data/knowledge/ats_criteria.md` is not tracked. Copy the current file to
`data/knowledge/ats_criteria.md.bak` before editing; restore from there.
The most recent backup zip in `data/backups/` also contains the original.