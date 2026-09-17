# Implementation Plan — Persona Governs Voice, Not Content

**Branch:** `fix/persona-voice-only` · **Base:** `master` @ `455e5ef`
**Not in the work order.** Found while reviewing a generated letter.

## Intent

`persona.md` contains specific project claims. The writer treats them as source
material and asserts them as the candidate's experience.

Measured across all 22 stored letter versions (16 companies), and separately
across the 12 written since `cv.md` was rewritten on 2026-09-07 (7 companies).
Letters before that date were written against the old `cv.md`, preserved in
`data/backups/backup_2026-09-02_123407.zip`, which did evidence Work Breakdown
Structures and a side-by-side visual comparison. Those two phrases were grounded
when written; the rest never were.

| Phrase | All letters (versions / companies) | Since 2026-09-07 (versions / companies) | In old `cv.md`? | In current `cv.md`? |
| --- | --- | --- | --- | --- |
| "first-attempt certification" | 14 / 11 | 9 / 6 | no | no |
| "timeline risk" | 8 / 5 | 8 / 5 | no | no |
| "gracefully" | 4 / 3 | 4 / 3 | no | no |
| "materialized" | 2 / 2 | 2 / 2 | no | no |
| "hardware redesign" | 1 / 1 | 1 / 1 | no | no |
| "renegotiated" the budget | 1 / 1 | 1 / 1 | no | no |
| "developer capacity", "honest client negotiation" | 1 / 1 | 0 / 0 | no | no |
| "side-by-side" | 11 / 10 | 1 / 1 | **yes** | no |
| "Work Breakdown Structure" / "WBS" | 11 / 10 | 1 / 1 | **yes** | no |

**Never in either CV:** first-attempt certification, timeline risk, gracefully,
materialized, hardware redesign, renegotiated, developer capacity, honest client
negotiation. They appear in 15 versions across 12 companies — 9 versions across 6
companies since the rewrite.

The clearest case is a letter written on 2026-09-08, after the rewrite, reading
*"I mapped a side-by-side Work Breakdown Structure, proved the added complexity to
the Product Owner, and renegotiated the budget based on physical data"* —
`persona.md` line 57's example rewrite, near-verbatim, presented as the
candidate's own anecdote. Line 57 paraphrases a simulator-project story from the
**old** `cv.md`. The 2026-09-07 rewrite removed that story from the CV; the
persona kept it, so it became a claim with no evidence behind it.

**This narrows a governance rule; it is not a bug fix.** Both governing documents
permit the persona as a fact source:

- `PROJECT_INSTRUCTIONS.md:152`: "**Cover letters may only recombine facts present
  in `cv.md` and `persona.md`.**"
- `ATS_DOMAIN_BRIEF.md` §5, lines 169–170: "The letter may only recombine facts
  present in `cv.md` and `persona.md`."

Only the writer's rule 2 says CV only: "explicitly present in the CV section
below". The model broke that rule while staying inside the two documents' rule:
`persona.md` sits in the same prompt as `cv.md` and reads as source material about
the candidate, not as instructions about style.

The current `cv.md` evidences a game port, a fashion-asset engagement shipping on
schedule and under budget from the third collection, and delivery lead on a
simulator project. It does not evidence first-attempt certification, any WBS, a
hardware redesign, or a budget renegotiation.

> Correction, 2026-09-14: this section originally said both governing documents
> require letters to assert only what `cv.md` evidences, said "the model obeyed
> all three", and counted across all letters without separating those written
> against the old CV. All three are corrected above.

**A second defect, found while verifying this batch.** The first letter
generated after the persona fix opened "Olá equipa Blip.pt," with an English
body and an English sign-off. The description is English — 190 English function
words, 0 Portuguese, 0 non-ASCII letters — and the only Portuguese word in the
whole letter was "equipa". The company value is "Blip.pt".

Rule 5 already excluded the company from the language decision, but rule 6 is
where the model is looking when it writes the salutation, so the exclusion was
restated at the point of use: a Portuguese-looking company name does not make a
Portuguese letter, and the salutation, body and sign-off are always in the same
language as each other.

> **That fix was superseded inside this same batch.** The next regeneration of the
> same posting produced a Portuguese salutation *and* a Portuguese sign-off around
> an English body — a third failure of the language rule, on the same posting,
> against the instruction requiring all three parts to share a language. The rule
> was not binding at all, so Portuguese was withdrawn from the writer entirely and
> these rule 6 sentences were removed with it. The shipped prompt offers one
> salutation and one sign-off, both English. See
> `docs/plans/english-only-letters.md`, which ships as the same commit.

This is the third time a language or content cue has beaten a rule stated
elsewhere in the prompt — after `persona.md`'s Portuguese cues overriding rule 5,
and `persona.md`'s project claims overriding the anti-fabrication rule. The
pattern: a rule stated once, far from where the model acts on it, loses to
whatever is nearer.

## Design

**Anything in `persona.md` that prescribes *what to say* is removed. What
prescribes *how to say it* stays.**

The file already carries a line stating it governs voice and never language,
added when a persona cue overrode the writer's language rule. The same principle
extends to content.

Five changes to `data/knowledge/persona.md`, plus a new rule. Line numbers are as
they were before this batch; the new rule at the top of §1 shifts every later
line down by two.

**Line 19** — "If a project was delayed (e.g., *a named game port*), state
clearly that the timeline risk materialized but was managed gracefully to protect
quality and secure first-attempt certification." This names a project, asserts an
outcome, and supplies wording. It becomes a rule about handling setbacks with no
project, no outcome and no phrasing: state what happened and what was done about
it, using only what `cv.md` evidences.

**Line 20** — its second sentence, "Use physical deliverables, process
improvements, and stakeholder alignments as your source of truth", names a source
of truth that competes with `cv.md`. It becomes a sentence naming `cv.md` as the
only one. The "increased efficiency by 43%" counter-example stays: no stored
letter contains "43".

**Line 11** — "Celebrate human effort, direct developer capacity, and honest
client negotiation." Both phrases appeared verbatim in a letter as the
candidate's own claims, and neither was in either CV. It becomes a line about
crediting people's work without inflating it.

**Line 57** — the mandated voice example is a specific anecdote with named
artefacts. It is replaced with an example carrying the same voice — short
declarative opener, then a multi-clause explanatory sentence — built from
material that is not in `cv.md` and could not be mistaken for the candidate's
history. Generic enough to demonstrate cadence, specific enough to be a useful
model. The replacement is a ferry missing its tide, told in the third person so
it cannot be read as the candidate's anecdote. Every noun in it was searched in
`cv.md` as a whole word; none appears.

**Line 53** — the prohibited example names a simulator project that *is* in
`cv.md`. It is a negative example, so the risk is lower, but it is replaced for
the same reason: the same corporate write-up, about the ferry timetable, pairing
with the new line 57.

**A new rule at the top of §1:** this document supplies voice, not facts. Every
concrete claim in a document comes from `cv.md`. Any project, metric, outcome or
phrasing appearing in this file is illustrative and must never be asserted.

**Left as is**, after a line-by-line read for other content prescriptions: line
10 (topics to emphasise, no facts), line 21 (a no-apology stance), line 31 (the
explanatory sentence's topic list, inside the style mechanics kept out of scope)
and line 46's example verbs (inside the forbidden-vocabulary section, also out of
scope). Four of line 46's verbs were used by the old `cv.md`; since the rewrite
only one of them has appeared in a letter.

### Decisions, approved 2026-09-14

- **D1 — the governance rule is narrowed, and recorded.** `PROJECT_INSTRUCTIONS.md`
  and `ATS_DOMAIN_BRIEF.md` each gain a status-header bullet stating that the
  persona is no longer a fact source.
- **D2 — line 20's second sentence** is replaced.
- **D3 — line 11** is replaced. It has already leaked verbatim.
- **D4 — this plan names no CV project.** References to the candidate's projects
  are generic, per the convention in `docs/plans/project-docs-into-repo.md`.

## `persona.md.example`

Tracked, and it would teach a forker nothing about this — it has no examples and
no content rules, so it cannot cause the defect. It gains one line making the
principle explicit, because someone writing their own persona from it will
naturally reach for their own anecdotes.

## Scope — in

1. **`data/knowledge/persona.md`** — the five changes and the new rule above.
   Gitignored, so this lives in the working tree only.
2. **`data/knowledge/persona.md.example`** — one line stating that the persona
   supplies voice and never facts, and that examples must not be drawn from the
   candidate's real history.
3. **`src/skills/writer.py`** — `SYSTEM_PROMPT` rule 2, the anti-fabrication
   rule, currently says claims must be present "in the CV section below". It
   gains one sentence: the Persona section is not a source of facts, and nothing
   appearing only there may be asserted. Rule 6 is not touched by this batch —
   see the note at the end of the second-defect section below.
4. **`docs/PROJECT_INSTRUCTIONS.md`** — one status-header bullet: line 152 is
   narrowed to `cv.md` only (D1).
5. **`docs/ATS_DOMAIN_BRIEF.md`** — the same bullet, citing §5 (D1).
6. **`tests/test_writer_persona_not_facts.py`** — new. Asserts that rule 2 names
   the Persona section as not a source of facts. No existing test is modified.

## Existing letters

Not regenerated. 19 of the 22 stored letter versions contain at least one of the
phrases above, 9 of them written since the 2026-09-07 rewrite; 15 versions carry a
phrase that was never in either CV. The database has no "sent" field: 8 of the 19
are on jobs marked applied, 4 of them since the rewrite. Regenerating costs one
LLM call each and does not unsend anything. Recorded in the Backlog with the phrases to search for, so any letter
about to be reused can be checked.

## Scope — explicitly out

- `cv.md`. Whether the claims are true is the user's to judge; the CV is the
  authority the rules bind the model to, and changing it to accommodate the
  letters would invert that.
- Regenerating stored letters.
- `persona.md`'s style mechanics: the 8.7-word target, the 30/70 split, the
  forbidden vocabulary. All voice, all staying.
- The "stop writing" rule at line 47, already handled by the writer's rule 4.
- `ats_criteria.md`, the scorer, the PDF exporter.
- Anything on the existing backlog.

## Acceptance criteria

1. `py -m pytest -q` passes. No existing test modified.
2. A new test asserts `SYSTEM_PROMPT` names the Persona section as not a source
   of facts.
3. `Select-String -Path .\data\knowledge\persona.md -Pattern "first-attempt|Work Breakdown|side-by-side|gracefully|timeline risk|developer capacity|source of truth"`
   returns nothing, and neither does a search for the two project names the old
   examples used (not written here, per D4).
4. Manual: generate a letter and confirm it contains none of those phrases.
5. Manual: the letter still reads in the persona's voice — short declarative
   openers, varied sentence length, no AI clichés.
6. Manual: a letter for a posting whose company name looks Portuguese opens
   "Hello COMPANY team," and closes "Kind regards,".

## Backlog

- **Stored letters contain unevidenced claims.** Search any letter before reusing
  it: "first-attempt", "side-by-side", "Work Breakdown", "timeline risk",
  "gracefully", "materialized", "hardware redesign", "renegotiated", "developer
  capacity", "honest client negotiation". 15 companies are affected all-time, 6
  since the 2026-09-07 rewrite. "side-by-side" and "Work Breakdown" in letters
  written before the rewrite were grounded in the old CV. A separate cluster in
  those older letters — a client project with a communication breakdown, scope
  creep and a technical impact analysis — also came from the old CV, is not a
  persona defect, and is left off this list.
- **`persona.md` is gitignored**, so this fix exists only on this machine. A fork
  writing its own persona could reintroduce the defect; `persona.md.example`'s
  new line is the only guard.

## Rollback

`git checkout master`, delete the branch. Back up `persona.md` to
`persona_pre_voice_only.md.bak` before editing — `*.bak` in that directory is
gitignored.