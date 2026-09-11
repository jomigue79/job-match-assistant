# Implementation Plan — Edit a Cover Letter

**Branch:** `feat/edit-letter` · **Base:** `master` @ `dc3b1a5`
**Not in the work order.** User-requested. Prerequisite for PDF export — there is
no point exporting a letter you cannot correct first.

## Intent

A generated letter is read-only. The modal shows it in a `ui.label`
(`page.py:410-413`) with Copy and Close. A wrong date, a company name spelled
differently in the posting, a sentence that reads badly — none of it can be
fixed without regenerating the whole letter and losing everything you liked
about it.

## Design

The modal's label becomes a `ui.textarea`, plus a Save button.

**An edit overwrites the current version. It does not create a new one.**

`save_cover_letter` (`service.py:356-390`) inserts `MAX(version) + 1`, and the
Regenerate button calls the same handler. So the version number already carries
meaning: **v2 means the LLM wrote it again.** All 17 stored letters are v1
because no letter has ever been regenerated. If an edit also bumped the version,
that meaning would be lost — v3 could be a regeneration or a comma.

**`created_at` is not touched.** It is what the Applied tab sorts by
(`build_view_state`, newest letter first) and what the card displays as "Letter
written: <date>". An edit that moved it would reshuffle the application list
every time a typo was fixed. The field means "when this letter was generated",
and editing does not change that.

**This is the first destructive write in the project.** Everything else either
inserts, or updates a status through a validated transition. Stated plainly
because it is a real departure: the previous text is gone, with no undo. Accepted
— the alternative is version inflation that destroys the one signal the version
number carries.

### The dialog reads the letter from the database when it opens (D2)

**A correctness fix, not a preference.** The original design passed the dialog
the `MatchCard` captured when the card was rendered. Cards are rebuilt only when
`cards_signature` changes (`page.py:873-877`), and that signature is built from
status counts (`page.py:172`) plus `generating_hashes`. An edit changes no status
and no count, so no rebuild ever happens. **The original design would have
shipped a View button holding pre-edit text indefinitely** — reopening a saved
letter would show the old text, and Copy would copy it. Worse, because the write
is destructive, **a second edit made on that stale text would silently revert
the first.** Acceptance criterion 3 would have failed deterministically.

The dialog now calls `get_latest_cover_letter` on open and never trusts
`m.letter_text`. No refresh is needed after Save: an edit changes no count, so
`refresh()` would rebuild nothing anyway.

### Save is guarded by the version the dialog opened (D1)

Regenerate disables only its own button while generating; the View button stays
enabled (`page.py:731-738`). So the user can click Regenerate, open View on v1
while generation runs, let v2 land, and then Save. An update that simply targeted
`MAX(version)` would write the edited v1 text over the fresh regeneration.

`update_cover_letter` takes an optional `expected_version`. The dialog passes the
version it opened; if the current version differs, the update raises and writes
nothing, and the dialog stays open with the edited text.

## Scope — in

1. **`src/persistence/service.py`** — new
   `update_cover_letter(identity_hash, text, expected_version=None) -> CoverLetter`:
   - Selects the row with `MAX(version)` for that hash.
   - Raises if no letter exists — this is an edit, not a create.
   - Raises if `expected_version` is given and is no longer the current version.
   - `UPDATE cover_letters SET text = ? WHERE identity_hash = ? AND version = ?`.
   - Raises and rolls back if the UPDATE changed no row. `_write_lock` serializes
     this app's writes, but not other connections: a row deleted outside the app
     between the SELECT and the UPDATE would otherwise be reported as updated.
   - `created_at` is not in the SET clause.
   - Under `self._write_lock`, via `asyncio.to_thread`, matching every other
     write in the file.
   - Returns the updated `CoverLetter`.

2. **`src/ui/page.py`**
   - `show_letter_dialog` reads the letter from the database on open (D2) and
     shows the version it read.
   - Its `ui.label` (`:410-413`) becomes a `ui.textarea`, full width, monospace,
     tall enough to read — the letter is ~1,600–2,300 characters, so a fixed
     height in the 55–60vh range.
   - A **Save** button beside Copy and Close. On click: disable, call the
     handler with the opened version (D1), notify, close.
   - Copy copies the **current textarea contents**, not `m.letter_text` — a user
     who edits and then hits Copy expects their edit.
   - The dialog is already `persistent` for Add Job; make this one persistent
     too, so a stray click cannot discard an edit in progress. Close then means
     Close only.
   - Errors notify and leave the dialog open with the edited text intact.

3. **A module-level handler**, mirroring `generate_cover_letter_handler`
   (`page.py:198`) so it is testable without a UI:
   `save_letter_edit_handler(identity_hash, text, persistence, expected_version=None)`.
   Rejects empty or whitespace-only text before writing; stores the text exactly
   as typed.

## Scope — explicitly out

- Viewing or restoring earlier versions. Nothing in the UI exposes them;
  `list_jobs_with_match` fetches `MAX(version)` only.
- Any schema change. `cover_letters` is unchanged.
- `save_cover_letter` and the Regenerate path. Regeneration still creates a new
  version, which is the behaviour that gives the version number its meaning.
- Editing the letter's job, score, or any other field.
- PDF export. Next batch.
- `n_errors`, token-rate defaults, `settings.py:47`, `conftest.py`, the
  location-in-hash defect.

## Acceptance criteria

1. `py -m pytest -q` passes. No existing test modified.
2. New tests cover: an edit updates the text of the current version; the version
   number does not change; `created_at` does not change; editing a job with no
   letter raises; empty text is rejected before any write; after an edit,
   `list_jobs_with_match` returns the new text. Also: only the highest version is
   edited; a stale `expected_version` raises and writes nothing; an UPDATE that
   changes no row raises; Regenerate after an edit creates v2; the Applied order
   is unchanged by an edit.
3. Manual: open a letter, change a word, Save, reopen — the change is there.
   Depends on D2.
4. Manual: the version badge still reads v1 after an edit.
5. Manual: Regenerate after an edit produces v2, and the edited text is gone —
   confirming regeneration and editing are different operations.
6. Manual: Copy after editing copies the edited text.
7. Manual: the Applied tab's ordering does not change when a letter is edited.

## Backlog

- **`cards_signature` is built from status counts only** (`page.py:172`, plus
  `generating_hashes` at `:874`). Any change to a job's content that leaves the
  counts untouched cannot trigger a rebuild, so anything a card captured goes
  stale. This is the second time it has forced a workaround: D2 here reads the
  letter on open instead of trusting the card. The first was cover-letter
  regeneration — a v1-to-v2 regeneration on an already-written job changes no
  count, and its card updates only because `generating_hashes` is folded into
  the signature. `docs/plans/tabbed-dashboard.md:101-103` records that folding
  as a constraint to preserve, not as a workaround; the characterization here is
  this plan's.

## Rollback

`git checkout master`, delete the branch. No schema change. Any text edited
before the rollback stays edited — that is the nature of a destructive write.
