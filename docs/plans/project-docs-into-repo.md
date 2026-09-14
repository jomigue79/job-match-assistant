# Implementation Plan — Project Documents into the Repository

**Branch:** `docs/project-documents` · **Base:** `master` @ `de8ab5f`
**Audit:** C7, partially. Not in the work order.

## Intent

Four documents govern this project and none of them is in the repository.
`PROJECT_INSTRUCTIONS.md`, `ATS_DOMAIN_BRIEF.md`, `WORK_ORDER.md` and
`DECISIONS.md` exist only as Claude Project knowledge. Six plans in
`docs/plans/` cite them — the rules about anti-fabrication, the invariants, the
audit IDs, the decisions D1, D6, D7, D8 and D9 (D2–D5 are cited nowhere) — and a
reader who clones the repository can open none of them.

`docs/HANDOFF.md` is in the repository and is cited alongside them, which makes
the gap easy to miss.

## The problem with a straight copy

All four are stale, in the same way the README was before it was rewritten.
Committing them unchanged would publish wrong facts as current.

**`PROJECT_INSTRUCTIONS.md`**
- "151 jobs, 151 match results, 10 cover letters, 11 runs" — 96 jobs were
  deleted deliberately on 2026-09-07, and the counts have moved since.
- "Stop and ask before any change that would require re-scoring more than 20
  jobs" — replaced by a $1 projected-cost threshold once per-job cost was known.
- Its working method predates the plan-then-approve loop actually in use, where
  the agent produces an implementation plan and waits for approval before
  writing code.

**`ATS_DOMAIN_BRIEF.md`**
- §2's dimension table describes criteria replaced by the rewrite in
  `docs/plans/batch-05-criteria-rewrite.md`.
- §4's calibration protocol was run and superseded: the finding was that the
  criteria measured the CV rather than the posting, so no threshold could
  separate the labels.
- §5 contains a null instruction — a pt-PT rule contrasting two byte-identical
  words — flagged at the start of this work and never corrected.

**`WORK_ORDER.md`**
- Batches 1, 3 and 5 are complete. Item 4.1 was struck. Batch 2 was re-scoped.
  Parts of batch 8 were absorbed into other work.

**`DECISIONS.md`**
- Holds D1–D9. D10 was drafted for the launcher and never filed.

## Approach

**Commit them with a status header, not silently corrected.** Each file gets a
short block at the top stating what is current, what is historical, and where
the superseding decision lives. The body is left as written.

Rewriting them to be accurate would erase the record of what was believed at the
time, and the plans in `docs/plans/` cite the originals. A reader needs to see
both the instruction and the fact that it moved.

**Exception: `DECISIONS.md` gains D10.** It was drafted in
`docs/plans/launcher.md` under "Decision log entry to add on merge" and never
filed. That is an omission, not a supersession.

## Scope — in

1. **`docs/PROJECT_INSTRUCTIONS.md`** — as written, plus a status header
   listing the three stale items above with pointers.
2. **`docs/ATS_DOMAIN_BRIEF.md`** — as written, plus a status header pointing
   §2, §4 and §5 at `docs/plans/batch-05-criteria-rewrite.md` and
   `docs/plans/scorer-computed-score.md`.
3. **`docs/WORK_ORDER.md`** — as written, plus a status header stating which
   batches are complete, struck, re-scoped or absorbed.
4. **`docs/DECISIONS.md`** — D1–D9 as written, plus D10 filed from
   `docs/plans/launcher.md`, plus a status header.
5. **`README.md`** — a short section pointing at `docs/` and saying what each
   document is for, including `HANDOFF.md`.
6. **The D-number collision is named, not fixed.** `DECISIONS.md` numbers its
   entries D1–D10, while `docs/plans/edit-cover-letter.md`,
   `docs/plans/letter-pdf-export.md` and `docs/plans/letter-salutation-signoff.md`
   each label their own approval choices D1–D5. Once `DECISIONS.md` is public,
   "(D1)" in one of those plans reads as a citation of DECISIONS D1. The
   `DECISIONS.md` status header names the collision and says the plan-local
   labels refer only to the plan they appear in; the plans are left as written.

## Personal data

Every file is checked before staging. `PROJECT_INSTRUCTIONS.md` and
`ATS_DOMAIN_BRIEF.md` describe a candidate's constraints and may name employers,
locations, languages or years of experience. The repository is public.

Anything identifying is replaced with a placeholder or removed. The documents are
being published for their reasoning, not their subject.

## Scope — explicitly out

- Correcting the bodies of any of the four documents. Status headers only.
- `docs/HANDOFF.md`, already committed and already carrying its own superseded
  guidance, noted in earlier plans.
- `data/knowledge/*.md`. Those are personal and gitignored by design.
- Any code, test or configuration change.
- Filing decisions D11 onward for work done since. The plans in `docs/plans/`
  are that record.

## Acceptance criteria

1. `py -m pytest -q` passes, unchanged at 305. No code is touched.
2. All four files exist under `docs/` and each opens with a status header.
3. `DECISIONS.md` contains D1 through D10.
4. No file under `docs/` contains the candidate's name, email, phone number,
   Google Sheet ID, API key, or any employer name from their CV. Verified by
   search before staging.
5. `README.md` links to all five documents under `docs/`.

## Rollback

`git checkout master`, delete the branch. Documentation only.

## Closing note

**Convention, established here.** Plans may name the companies whose postings
the tool scored — those are public job advertisements and the scores are the
user's own judgements. Plans must not name the candidate's own employers or
projects from `cv.md`. `docs/plans/batch-05-criteria-rewrite.md` names two
(committed in `52b4d7b`, already public); it is left as written, because
editing it would not remove them from history and would misrepresent what the
plan said at the time.