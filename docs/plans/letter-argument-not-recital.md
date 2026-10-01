# Implementation Plan — The Letter Argues, It Does Not Recite

**Branch:** `fix/letter-argument` · **Base:** `master` @ current HEAD
**Not in the work order.** User-reported: "it just seems that we are vomiting the
CV in text without any kind of focus."

## The defect

The letters are well written and structurally wrong. Measured against the
Metyis letter (score 89) and the Ankix letter (score 70):

| What research says a letter must do | What these letters do |
| --- | --- |
| Could not be sent to another company | Paragraphs 1–3 work for any PM job |
| At least three company-specific details | Zero; the company is named once, in the salutation |
| Add context, not restate the CV | It is the CV, in CV order, in prose |
| Be selective | Comprehensive: four years, six products, two AI tools, two certifications, three methodologies |
| Replace vague claims with a specific name or result | Every proper noun in the CV is stripped to a category |
| A hook in the first sentence | The job title restated |
| Problem-solution | No problem is named, so nothing is solved |

Sources: Michael Page on what recruiters look for; The Interview Guys' 2026
analysis of 80+ studies; StylingCV; ResumeVera; and a hiring manager's account of
reading 1000+ letters. All converge on specificity and selection.

## Two mechanical causes, both in the prompt

**1. The scoring notes are being used as an outline.** The writer receives the
scorer's five per-dimension reasons under the heading **"WHY THIS JOB MATCHES"**
(`writer.py:60-61`). Those reasons are written to justify a number, one per
dimension, and the letter mirrors them — in order, in substance, and sometimes in
wording.

Traced on the Metyis letter. The scorer's first reason reads: *"The posting
explicitly describes ownership of AI product lifecycle, translating business
requirements into technical specifications, defining acceptance testing,
coordinating implementations, and tracking performance."* The letter's fourth
paragraph reads: *"I define and manage acceptance testing, non-regression
protocols, and quality assurance processes, ensuring AI solutions perform as
expected in production."*

That is the job advertisement's own sentence, routed through the scorer, returned
to the employer with "I" in front of it. A hiring manager who wrote that ad will
recognise it.

**2. The notes are also acting as a fact source.** The same reason set asserts
*"practical experience with data governance (PM² for RAID governance)"* — the
scorer's inference, not a CV claim. The letter then asserts data-governance
experience outright. Rule 2 binds the letter to `cv.md`, but nothing tells the
model that the scoring notes are not part of the CV. This is the same failure as
`persona.md` supplying facts, in a different channel.

**3. Nothing instructs the letter to do anything else.** There is no rule
requiring a hook, requiring selection, preferring a named counterparty over a
category, forbidding reuse of the posting's wording, or handling a gap.
`ATS_DOMAIN_BRIEF` §5 specifies all of that and no prompt ever carried it — the
same pattern as the language rule, which §5 also required and which was first
implemented in `ab98608`.

## Decisions taken with the user

**Why this company, from the posting only.** The letter may use what the posting
says about **the work** — the responsibilities, the stated challenge, the scope,
the stack. It may not use what the company says about **itself** — its values,
culture, mission, size or ambitions. The first is a fact to respond to; the
second produces "a forward-thinking firm", which is worse than silence. Fetching
the company's own pages is backlog.

**Named counterparties, attributed correctly.** The CV names clients, brands and
platforms, and the letters strip them to categories. They go back in. But the
candidate managed these projects **as project manager for the employer delivering
the work**, not as the holder of the client relationship, and the prompt says so
explicitly. "I ran X for a named client" and "I was PM on my employer's work for
a named client" are different claims, and only the second is true.

**The gap, only when the requirement is hard.** One sentence, only where the
posting calls something required, essential or mandatory and the CV does not
evidence it. Never for a "nice to have", and never a weakness the posting did
not ask about.

**A skeleton, not a model letter.** The prompt shows the shape as an annotated
outline and supplies no finished prose. A worked example would be copied: that is
exactly what happened with `persona.md`'s example rewrite, which appeared
near-verbatim in letters for ten companies
(`docs/plans/persona-voice-not-content.md`).

**No CV content in the prompt.** `src/skills/writer.py` is tracked and the
repository is public. Every rule below is written generically — no employer,
client, brand, platform or project name from `cv.md` appears in the prompt or in
this plan.

## Scope — in

1. **`src/skills/writer.py` — `SYSTEM_PROMPT`.**
   - Rule 6 keeps the envelope: salutation, body, sign-off.
   - **New rule 7, what the body must do**: open on the work rather than the
     title; select two or three pieces of evidence; name what the CV names;
     attach the result the CV states; never reuse six or more consecutive words
     from the posting; one gap, only for a hard requirement; a short close.
   - **A shape skeleton**, bracketed, with no copyable prose.
   - **A precedence sentence** at the head of rule 7 (D1).
   - OUTPUT FORMAT moves to rule 8. No committed test asserts on a rule number
     above 6 or on OUTPUT FORMAT's text, so the renumbering breaks
     nothing and no test is edited.

2. **`src/skills/writer.py` — `USER_PROMPT_TEMPLATE`. — superseded by Addendum 1** The heading
   `WHY THIS JOB MATCHES` becomes `SCORING NOTES — CONTEXT ONLY`, with a
   preamble stating that the notes justify a score rather than outline a letter,
   that they quote the posting so rules 3 and 7(e) apply to their contents, and
   that they are **not a source of facts** — rule 2 still binds every claim to
   the CV. The notes also get their own delimiter (D2).

3. **`data/knowledge/persona.md.example`** — one line noting that letter
   structure is set by the writer's prompt, not by the persona, so a fork does
   not try to specify it here.

4. **`tests/test_writer_body_rules.py`** — new. Asserts rule 7's requirements,
   that the skeleton is five bracketed lines carrying no first-person pronoun,
   that OUTPUT FORMAT is rule 8 with no repeated rule number, and that the
   scoring notes are renamed, delimited and declared not a source of facts.

### Decisions, approved 2026-10-01

- **D1 — rule 7 takes precedence over a conflicting persona instruction.**
  Rule 4 requires following the persona's length and structure exactly, and rule 7
  sets length and structure too. The conflict is real and is resolved in the
  prompt rather than left to the model: "Rule 7 governs what the body says; the
  Persona section governs how it sounds. Where a persona instruction conflicts
  with rule 7, rule 7 wins."
- **D2 — the scoring notes get their own delimiter — superseded by Addendum 1**,
  `<scoring_notes_untrusted>`, alongside the prose preamble. They are **not**
  moved inside `<job_posting_untrusted>`: rule 3 describes that block as text
  published by the employer, and the notes are not that.
- **D3 — 7(e) keeps the six-word floor**, with the behavioural instruction
  first and the number second. The prompt is not expected to enforce it by
  counting; the number exists so that the mechanical checker, when built, and the
  prompt share one definition.
- **D4 — the `TASK` line is unchanged**, so
  `test_writer_letter_structure.py:180` keeps passing.

## Scope — explicitly out

- `persona.md`. Voice is not the defect: the letters read well sentence by
  sentence. The 8.7-word target, the 30/70 split and the forbidden vocabulary
  all stay.
- `cv.md`.
- The scorer, `ats_criteria.md`, the dimensions, the weights, `SCORE_THRESHOLD`.
- Regenerating the 22 stored letter versions.
- Fetching the company's own web pages to source a "why this company" line.
- The mechanical letter checker. Ship the prompt first; a checker written now
  would guard a shape nobody has seen output from yet. See Backlog.
- Letter length limits beyond the stated 200–300 words. The current letters are
  already about the right length; focus is the problem, not size.
- Any schema, persistence, UI or export change.

## A finding recorded, not fixed

The scoring notes sit **outside** the `<job_posting_untrusted>` delimiters, and
they are model output derived from attacker-controlled text that frequently
quotes it verbatim. Rule 3 protects what is inside the tags. This batch states in
the notes' own preamble that rules 3 and 7(e) extend to them, and wraps them
in their own `<scoring_notes_untrusted>` delimiter (D2), so the boundary is
marked rather than only described. Sanitising them, or having the scorer emit
notes that do not quote the posting, is backlog.

## Acceptance criteria

1. `py -m pytest -q` passes. No existing test modified — report and stop if one
   fails.
2. New tests assert: rule 7 exists and names the open-on-the-work, selection,
   named-counterparty, result, no-reuse, gap and close requirements; the
   skeleton contains no sentence a model could copy as prose; `SYSTEM_PROMPT`
   still contains no `{` or `}`; the scoring-notes preamble states they are not
   a source of facts — superseded by Addendum 1; the heading `WHY THIS JOB MATCHES` is gone.
3. A search of src for the CV-name pattern (held outside the repository) returns nothing.
4. Manual: a letter generated for a posting opens on something the posting says
   about the work, not on the job title.
5. Manual: it names at least one client, brand, platform or product that the CV
   names, and attributes it as work done for an employer rather than as the
   candidate's own client.
6. Manual: no sentence of six or more consecutive words appears in both the
   letter and the posting.
7. Manual: three paragraphs or more are specific enough that the letter could not
   be sent to a different company unchanged.
8. Manual: a gap sentence appears only where the posting states a hard
   requirement the CV does not evidence.

## Backlog

- **A mechanical checker.** Flag a letter before the user sees it when it reuses
  six or more consecutive words from the posting, names nothing from the posting
  beyond the job title, names no counterparty the CV names, or exceeds a word
  cap. The user has said he cannot reliably judge a letter himself, so a
  structural check is the durable answer; it is easier to write against letters
  produced by the new prompt than ahead of them.
- **Scoring notes are untrusted-derived and sit outside the delimiters.**
- **"Why this company" from the company's own pages**, via `WebFetch` or the
  browser. One request per letter, sometimes returning nothing useful.
- **The 22 stored letter versions** keep the recital shape. Not regenerated.

## Rollback

`git checkout master`, delete the branch. Prompt text only: no schema change, no
migration, nothing persisted.

## Addendum 1 — after the first regenerated letter

### Findings (reference posting, letter regenerated on this branch)

1. Tenure merge. The CV summary states the PM years and the current role's AI work in separate sentences. The letter joined them with "including", placing the current role inside the earlier span. The CV is correct; the prompt has no attribution rule.
2. Selection by result. Rule 7(b) says "select" without a criterion; 7(d) says "attach the result". The model selected the CV item with the strongest stated result and told it as delivery, although the CV lists the same item as stakeholder work and the posting names stakeholders six times.
3. Methodology by CV order. The posting asks for agile; the CV evidences agile in three places and a governance framework first. The letter put forward the framework.
4. Gap copied from the scoring notes. The scorer's note called a requirement a "minor gap"; the letter reproduced it although the requirement is not marked hard (7(f)). This is the third observed failure of the scoring notes as writer input (outline, fact source, gap source), after the delimiter and preamble were added.
5. Opening and closing paraphrase the posting's first responsibility (7(a), 7(e)).

### Decision: the writer no longer receives the scoring notes

HR: the notes are the scorer's judgement of the posting, not evidence about the candidate; everything the writer legitimately needs from them is in the posting itself.
Architect: a guard on an input that keeps leaking is weaker than removing the input. This removes the class of bug, not one instance.
Scope: writer.py stops interpolating match_result.match_reasons. The generate() signature is unchanged so the UI handler and FakeWriter are untouched; removing the now-unused parameter is a separate later change. The notes remain stored and displayed on the card.

### Rule 7 amendments

(a) The opening sentence presents the candidate's strongest evidence for the first selected requirement. It does not describe the role, the field, or the posting's responsibilities.
(b) Select by the posting's emphasis. Before writing, identify the one or two requirements the posting states most often or most prominently. Each body paragraph argues one of them. Choose CV evidence because it proves that requirement, not because it carries a result. When one CV item evidences several things, present it under the requirement it is being used to prove.
(d) When the CV states a result for a selected item, attach it. A result never decides which item is selected.
(g) Close in one or two sentences. The closing does not restate a responsibility from the posting.
(h) NEW — Methods. Name a methodology, framework or certification only if the posting asks for it and the CV evidences it. Do not put forward one the posting does not mention, even when the CV lists it first.
(i) NEW — Tenure. Attribute every duration to the role that produced it. Never join statements about different roles with "including", "with", "during which" or any construction that places one role's work inside another role's time span. Describe the current role as current.

### Backlog (separate branches)
- Scorer equates a project-risk governance framework with data governance; inflates Requirements coverage.
- Remove the unused match_result parameter from Writer.generate() and its callers.

### Manual acceptance (reference posting, regenerated)
- Opening is evidence for the most-repeated requirement, not a description of the role.
- No duration spans two roles.
- The methodology named is the one the posting asks for.
- No gap sentence (the posting marks nothing as required).
- Closing restates no posting responsibility.

## Addendum 2 — structure: thesis, two arguments, close

### Findings (reference posting, letter regenerated after Addendum 1)
Passed: tenure attribution, no false gap, short close.
Failed:
1. One argument, then recital. The first paragraph argued the posting's most emphasised requirement; the rest followed CV order. Rule 7(b) allowed "one or two" requirements; the model took one and filled the word count with the CV.
2. The second most emphasised requirement received no paragraph, although the CV evidences it with named counterparties.
3. A CV line bundling a governance framework, risk logs, stakeholder alignment, documentation and agile methods was reproduced whole, defeating 7(h).

### Decisions (user, 2026-10-01)
- Exactly two arguments, one paragraph each.
- Body length stays 200 to 300 words.
- The letter opens with intention, not an example: a short thesis paragraph that names the role and previews the two arguments.
- Methods and certifications appear only if the posting asks for them and the CV evidences them (7(h), unchanged).
- The persona's paragraph rules and its sentence-length target yield to rule 7. Section 3B's 30/70 split and three-sentence ceiling are replaced by a line pointing at rule 7, and the 8.7-word average becomes roughly 13 to 18 words with a 25-word ceiling — at 8.7 words a four-paragraph letter cannot reach 200. The persona's pivot pattern is kept with ranges of 5—10 and 15—25 words; its list of things to explain is removed as a fabrication prompt. persona.md is gitignored, so that half lives only on this machine; persona.md.example loses its paragraph count and word cap for the same reason.

### Rule 7 amendments
(a) Open with a thesis. The opening is its own paragraph of one or two sentences: it names the role and states, in plain terms, the two things the candidate brings to it — the two requirements argued below. The role's title may appear inside this sentence. Never open with the title alone, never open with a phrase such as "I am writing to", and never describe the company back to itself. The thesis previews; it asserts nothing the two evidence paragraphs do not prove.
(b) Exactly two arguments. Select the requirement the posting states most often or most prominently, and the next most emphasised requirement of a different kind. The body has exactly two evidence paragraphs, one per requirement, in that order. Each paragraph argues only its own requirement and proves it with named work from the CV. If a paragraph needs more length, go deeper into the same requirement — never add another CV item to fill space. Leave the rest of the CV out; the CV is already attached.
(j) NEW — Partial use. Take from a CV line only the part that proves the paragraph's requirement. Never reproduce a CV line's list of methods, tools, activities or certifications.

### Skeleton
[Opening: the role, and the two things the candidate brings to it — the two requirements argued below.]
[Evidence 1: the first selected requirement, proven with named work from the CV and the result the CV states.]
[Evidence 2: the second selected requirement, proven with named work from the CV, counterparties attributed as the CV attributes them.]
[Gap: only if a hard requirement is unevidenced — name it, then the nearest evidenced experience.]
[Close: what the candidate would take on here, and availability.]

### Manual acceptance
The reference posting plus one other project-manager posting, two regenerations each (four letters). Each letter:
- Opening paragraph names the role and previews two arguments; no "I am writing to".
- Exactly two evidence paragraphs, each on one requirement, in posting-emphasis order.
- No CV line reproduced as a list; no method or certification the posting does not ask for.
- Body 200–300 words.
- Addendum 1 checks still hold (tenure, gap, close).