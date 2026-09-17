# Implementation Plan — English-Only Letters

**Branch:** `fix/english-only-letters` · **Base:** `master` @ `455e5ef`
**Not in the work order.** Found while verifying
`docs/plans/persona-voice-not-content.md`.
**Ships as one commit with that batch.** `writer.py` carries both changes, and the
rule 6 sentence the persona batch added is deleted by this one; committing them
separately would add a sentence and remove it two commits later.

## Intent

**Three attempts at a language rule, three different failures.** The rule is not
binding on the model, and the cost of it being wrong is paid on a letter that
gets pasted into a real application. English only is predictable; a Portuguese
application can be translated by hand.

| # | Attempt | What the model did | Evidence |
| --- | --- | --- | --- |
| 1 | Rule 5 decides the language from the Description alone | Wrote an entire letter in European Portuguese for an English posting, following the persona's Portuguese cues instead | Playtech v3, 2026-09-14 10:39 — description 653 words, 138 English function words, 2 Portuguese; letter body 161 words, 13 Portuguese, 0 English |
| 2 | Rule 5 reworded to override the persona, and the persona's own language cues removed | Took the language from the company name instead: a Portuguese salutation on an English body, with an English sign-off | Blip.pt v1, 2026-09-14 17:13 and v2, 2026-09-17 07:41 — description 950 words, 190 English function words, 0 Portuguese, 0 non-ASCII letters; salutation "Olá equipa Blip.pt,", sign-off "Kind regards," |
| 3 | Rule 6 part (a) restates the exclusion at the point of use, and requires salutation, body and sign-off to share one language | Wrote a Portuguese salutation **and** a Portuguese sign-off around an English body | Blip.pt v3, 2026-09-17 07:42 — salutation "Olá equipa Blip.pt,", sign-off "Com os melhores cumprimentos,", body 200 words, 0 Portuguese, 32 English function words |

The only Portuguese in failures 2 and 3 is wording the prompt itself supplied:
"equipa" in the salutation and the closing phrase. The model was not writing
Portuguese prose — it was picking the Portuguese branch of a two-branch
instruction. Removing the branch removes the failure mode.

> Note on failure 3: v2 and v3 were generated fifteen seconds apart, and the app
> must be restarted to pick up a prompt change (D10, `reload=False`). Whether v3
> ran against the amended rule 6 cannot be established from the database; it is
> recorded here as reported.

**The pattern across all three.** A rule stated once, away from where the model
acts on it, loses to whatever is nearer — the persona, then the company name,
then the fixed phrase in the adjacent branch. The same pattern produced the
persona-as-fact-source defect. Where wording cannot make a rule binding, the
alternative is to remove the choice.

## Design

**`SYSTEM_PROMPT` offers no language choice.**

- **Rule 5** becomes one instruction: write the entire letter in English —
  salutation, body and sign-off alike — whatever language the job description,
  the company name, the location or the candidate's name is in. Nothing in the
  job details, the persona or the CV changes it.
- **Rule 6 part (a)** keeps only "Hello COMPANY team,", and "Hello," when the
  Company value is missing or reads "Unknown Company".
- **Rule 6 part (c)** keeps only "Kind regards," followed by the CANDIDATE NAME
  value.
- Every mention of European Portuguese, "equipa", "equipe", "Olá" and
  "Com os melhores cumprimentos" is gone from the prompt. Rules 1, 2, 3, 4 and 7
  are untouched.

No code decides a language any more, and no branch is left to take.

## What this narrows

Two governing documents require the letter to match the posting's language. This
batch withdraws that, the same way `persona.md` as a fact source was narrowed in
`docs/plans/persona-voice-not-content.md`:

- `PROJECT_INSTRUCTIONS.md` lines 158–161: "**Match the language of the
  posting.** Portuguese posting → European Portuguese (not Brazilian orthography
  or vocabulary). English posting → English. Never mix."
- `ATS_DOMAIN_BRIEF.md` §5, lines 150–154: "**Language.** Match the posting.
  Portuguese posting → **European Portuguese** … When a Portugal-based posting is
  written in English — common in tech in Porto and Lisbon — write in English."

Each gains one status-header bullet, in the style of the bullets already there:

```
PROJECT_INSTRUCTIONS.md:
> - **"Match the language of the posting" is withdrawn.** Every letter is
>   written in English, whatever language the posting is in. Three attempts at a
>   language rule failed in three different ways — the persona's cues, then the
>   company name, then the closing phrase — and a predictable English letter the
>   user translates himself is worth more than a rule the model does not follow.
>   See `docs/plans/english-only-letters.md`.

ATS_DOMAIN_BRIEF.md:
> - **§5's language rule is withdrawn.** Every letter is written in English,
>   whatever language the posting is in. Three attempts at a language rule failed
>   in three different ways — the persona's cues, then the company name, then the
>   closing phrase — and a predictable English letter the user translates himself
>   is worth more than a rule the model does not follow. See
>   `docs/plans/english-only-letters.md`.
```

The scoring criteria are unaffected. §1's language gate still fires on a posting
that requires a working language the candidate does not have; that gate is about
the job, not about the letter.

## Cost

A Portuguese-language posting now gets an English letter. The user translates it
himself before sending, or sends it in English. That is a real loss on postings
written in Portuguese, accepted because the alternative on offer is a letter
whose language is decided unpredictably — and a half-Portuguese letter reads
worse to a recruiter than a consistent English one.

## Scope — in

1. **`src/skills/writer.py`** — `SYSTEM_PROMPT` rule 5, and rule 6 parts (a)
   and (c).
2. **`docs/PROJECT_INSTRUCTIONS.md`** and **`docs/ATS_DOMAIN_BRIEF.md`** — one
   status-header bullet each, applied as drafted above.
3. **This plan.**

## Tests

Three committed tests in `tests/test_writer_letter_structure.py` asserted the
Portuguese wording and failed against the new prompt. They were correct tests of
the old rule, so they were reported and rewritten on the user's decision, in the
same file — that is where the prompt's structure rules are pinned:

- `test_system_prompt_requires_the_posting_language` — now asserts
  "5. LANGUAGE:", "Write the entire letter in English", and that "European
  Portuguese" is **absent**.
- `test_system_prompt_defines_both_salutations_and_sign_offs` — renamed
  `test_system_prompt_defines_one_salutation_and_sign_off`. Asserts
  "Hello COMPANY team," and "Kind regards," present, "Olá" and "cumprimentos"
  absent.
- `test_system_prompt_has_fallbacks_for_missing_company_and_name` — its
  salutation assertion becomes `write "Hello," instead`. The "Unknown Company",
  "(not provided)" and placeholder assertions are unchanged.

One test from the persona batch was deleted rather than rewritten:
`test_rule_six_keeps_the_company_out_of_the_language_decision` pinned the rule 6
sentences this batch removes. It was new and unstaged, so deleting it finishes
that batch rather than editing a committed test.

The suite passes at 328 tests.

## Scope — explicitly out

- Regenerating stored letters. Four versions carry Portuguese wording: three for
  one company, one for another.
- `persona.md`, whose line 4 already says the system prompt sets the language.
  That statement stays true.
- The PDF exporter. It encodes Latin-1, and an English letter signed with an
  accented name still encodes cleanly.
- The scorer, the criteria and the language gate.
- Anything on the existing backlog.

## Acceptance criteria

1. `Select-String -Path .\src\skills\writer.py -Pattern "Portuguese|equipa|equipe|Olá|cumprimentos"`
   returns nothing.
2. `py -m pytest -q` passes — after the three tests above are decided.
3. A test asserts that rule 5 requires English for the whole letter, and that
   rule 6 offers one salutation and one sign-off.
4. Manual: a posting written in Portuguese produces a letter opening
   "Hello COMPANY team,", closing "Kind regards,", with an English body.
5. Manual: a posting whose company name looks Portuguese does the same.

## Backlog

- **Four stored letter versions carry Portuguese wording**, one of them
  Portuguese throughout. They are not regenerated; any of them reused should be
  checked first.
- **The language gate and the letter language now disagree by design.** A posting
  may state Portuguese as a working language, pass the gate, and receive an
  English letter. If that costs a real application, the answer is a translation
  step the user controls, not a language branch in the prompt.

## Rollback

`git checkout master`, delete the branch. `writer.py` is tracked, so the revert is
clean, and no knowledge file is touched by this batch.
