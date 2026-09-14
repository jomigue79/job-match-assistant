# Implementation Plan — Salutation and Sign-off

**Branch:** `feat/letter-salutation` · **Base:** `master` @ `778fba7`, on top of
the uncommitted `feat/letter-pdf` work.
**Not in the work order.** Found while reviewing the first exported PDF.

## Intent

Generated letters have no salutation and no sign-off. `Writer.SYSTEM_PROMPT`
rule 4 says "Return ONLY the cover letter text, ready to send", and `persona.md`
specifies voice and structure but never mentions opening or closing a letter. So
the model produces body paragraphs and nothing else.

The first exported PDF made this visible: a header, a rule, four paragraphs, and
then it stops. No greeting, no name at the bottom.

**This is fixed in the writer, not the exporter.** Adding a salutation at export
time would put text in the PDF that is not in the letter, and the version copied
to the clipboard would still be missing both. Fixing the prompt means every
destination — modal, clipboard, PDF — gets a complete letter.

## Design

**Salutation: `Hello <Company> team,`** — the company is always on the job
record, so there is no dependence on the posting naming a hiring manager, which
it usually does not.

**Sign-off: a closing phrase, then the candidate's name**, using
`CANDIDATE_NAME` from settings — the same value the PDF header uses.

**Both follow the letter's language:**

| | English | European Portuguese |
| --- | --- | --- |
| Salutation | `Hello Playtech team,` | `Olá equipa Playtech,` |
| Sign-off | `Kind regards,` | `Com os melhores cumprimentos,` |

`equipa`, not `equipe`.

### The writer did not follow the posting's language (D2)

**This plan originally assumed the writer already matched the posting's
language. That premise was false.** No rule in `Writer.SYSTEM_PROMPT`,
`USER_PROMPT_TEMPLATE` or `persona.md` instructs it. PROJECT_INSTRUCTIONS and
ATS_DOMAIN_BRIEF — project documents outside this repository — both require
language matching; no prompt ever implemented it.

The stored data confirms it. A read-only count of Portuguese accented characters
and function words over the database found:

- 31 of 145 postings appear to be written in Portuguese.
- Three of them have letters, with 22, 22 and 78 Portuguese function words in the
  posting.
- All 17 stored letters are in English, those three included.

**This was a live defect, not a gap the salutation work created.** The salutation
rule depends on the letter's language, so this batch adds an explicit language
rule (rule 5). Its effect goes beyond the salutation: future letters for
Portuguese postings will be written in European Portuguese throughout, where
today they are English. Postings in any other language, or of unclear language,
still get English.

### The company never enters the system prompt

`SYSTEM_PROMPT` stays a constant with nothing interpolated. **The company name is
never placed in it**, for two reasons:

- Rule 3, the untrusted-data rule, protects only text **inside** the
  `<job_posting_untrusted>` delimiters. A company name interpolated into the
  system prompt would sit outside them, unprotected.
- The system prompt is the highest-authority channel. A company named
  `Ignore Previous Instructions Ltd` would appear among the model's own
  instructions.

Instead, rule 6 tells the model to copy the Company value from the delimited job
details — which rule 3 already permits as using the text as factual information.
The salutation will literally contain whatever the company field says, which is
correct: it is the company's recorded name, confined to one line.

### Missing values

- **Blank `CANDIDATE_NAME` (D3):** the prompt renders `(not provided)`, and rule 6
  says to close with the phrase alone. An empty field is ambiguous whitespace in a
  prompt, and a letter ending in a placeholder is worse than one ending in a
  closing phrase.
- **Missing company (D4):** the writer's existing fallback renders
  `Unknown Company`. Rule 6 turns that, or a missing value, into `Hello,` /
  `Olá,` rather than `Hello Unknown Company team,`.

### Persona interaction

`persona.md` enforces a 30/70 paragraph split and says to "State the final
result clearly and stop writing." Rule 4 now states that the salutation and
sign-off are not paragraphs, do not count toward the persona's paragraph or
sentence rules, and are not removed by any persona instruction.

### Persona language cues were overriding rule 5

**Found after implementation.** Rule 5 as first written — write "in the language
of the job description" — lost to the persona. The Playtech posting is written in
English: 131 English function words to 2 Portuguese hits, and both of those are
`com` split out of `.com` addresses. A letter generated for it in a manual test
came out in Portuguese.

`persona.md` is interpolated into the **user** prompt, and it carried three
Portuguese cues competing with a **system**-prompt rule:

- line 3 names the persona after a Portuguese candidate;
- line 12 asked for "sardonic Portuguese humor" — meant as a flavour of wit, read
  by the model as a language instruction;
- the mandated voice example at the end of the file is a status report, not a
  letter.

Two changes follow:

- **`persona.md`** — "sardonic Portuguese humor" becomes "sardonic humour", and a
  new line after line 3 states that the document governs voice, tone and prose
  mechanics only, never the language a document is written in, which the system
  prompt sets. Nothing else in the file changed: the anti-fabrication rules,
  sentence metrics, paragraph ratios and forbidden vocabulary are untouched. Line
  3's name and the status-report example remain as they were.
- **Rule 5** now decides the language from the Description inside the
  `<job_posting_untrusted>` tags and nothing else — not the candidate's name, the
  persona, the location or the company. It writes European Portuguese only for a
  Portuguese description and English in every other case, and it states that it
  overrides anything in the Persona section suggesting a language or a
  nationality.

**`persona.md` is gitignored, so its fix lives only in this working tree.** A
fork's `persona.md` is written by the forker, and it must not conflate voice with
language. `persona.md.example` was checked and **does not carry this warning**:
its sections are Voice & tone, What to emphasize, What to avoid, Structure &
length and Hard rules, and none of them mentions language. It should — see
Backlog.

## Scope — in

1. **`src/skills/writer.py`**
   - `SYSTEM_PROMPT` rule 4 gains the persona-interaction sentence; new rule 5
     (language) and rule 6 (letter structure); output format becomes rule 7 and
     says the letter runs from the salutation to the sign-off. Still a constant.
   - `USER_PROMPT_TEMPLATE` gains a `CANDIDATE NAME` field, between the CV and the
     `<job_posting_untrusted>` block — it comes from configuration, not from the
     posting. The TASK line points at the language and structure rules.
   - **`Writer.__init__` takes `candidate_name` (D1).** `generate`'s signature is
     unchanged. A blank name renders `(not provided)` (D3).
   - Rule 5 was strengthened after a manual test showed it losing to persona
     cues — see *Persona language cues were overriding rule 5*.

2. **`src/ui/main.py`** — constructs `Writer(candidate_name=settings.candidate_name)`
   (D1). `src/ui/page.py` and `generate_cover_letter_handler` are unchanged: the
   handler still calls `writer.generate(job, knowledge, match_result)`. Passing
   the name through the handler instead would break three existing tests in
   `tests/test_write_action.py`, whose `FakeWriter.generate` accepts no extra
   keyword.

3. **`data/knowledge/persona.md`** — two edits, working tree only, because the
   file is gitignored: line 12's "sardonic Portuguese humor" becomes "sardonic
   humour", and a new line after line 3 limits the document to voice, tone and
   prose mechanics. See *Persona language cues were overriding rule 5*. Letter
   structure still belongs in the prompt, where the company name and candidate
   name are available.

## Scope — explicitly out

- Regenerating the 17 existing letters. Each costs an LLM call and they are
  already sent or superseded. New letters are correct; old ones stay as they are.
- The PDF exporter. It renders whatever the letter contains; with this change
  that includes the salutation and sign-off.
- Naming a specific hiring manager. The scraper rarely supplies one and inventing
  one would breach the anti-fabrication rule.
- Recipient address blocks, dates in the letter body, subject lines.
- `cv.md`, `ats_criteria.md`. (`persona.md` was originally listed here too; it
  was changed after all — see *Persona language cues were overriding rule 5*.)
- Anything on the existing backlog.

## Acceptance criteria

1. `py -m pytest -q` passes. No existing test modified.
2. New tests assert: `SYSTEM_PROMPT` contains the salutation and sign-off rules;
   `USER_PROMPT_TEMPLATE` has a `CANDIDATE NAME` field; that field is **outside**
   the `<job_posting_untrusted>` delimiters; `generate` interpolates the name; an
   empty name still produces a valid prompt. Also: the language rule is present;
   `SYSTEM_PROMPT` contains no format placeholders and is sent unchanged for a
   hostile company name; braces in a company name are rendered literally.
3. Manual: a letter generated for an English posting opens
   `Hello <Company> team,` and closes with a phrase and the name.
4. Manual: a letter generated for a Portuguese posting opens
   `Olá equipa <Company>,` and closes in European Portuguese. Depends on rule 5.
5. Manual: the exported PDF shows both without any exporter change.
6. Manual: the Playtech posting, which is written in English, produces an English
   letter.

## Backlog

- **The 17 existing letters have no salutation, no sign-off, and are in English
  regardless of the posting's language.** Regenerating costs one LLM call each.
  Left as they are.
- **`persona.md.example` does not warn against conflating voice with language.**
  A fork writing its own persona could reintroduce the defect fixed here. It
  should carry the same sentence the live `persona.md` now has.

## Rollback

`git checkout master`, delete the branch. No schema change, nothing persisted.
Letters generated under this change keep their salutation and sign-off.
