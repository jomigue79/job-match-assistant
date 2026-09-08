# Implementation Plan — Tabbed Dashboard

**Branch:** `feat/tabbed-dashboard` · **Base:** `master` @ `cd240b4`
**Not in the work order.** User-requested.

## Intent

The dashboard is one scrolling column. Strong Matches, Applied Jobs, Non-Matches,
Pending and Rejected all render into a single container
(`src/ui/page.py:544-602`), so reaching applications means scrolling past every
match card. Split into tabs so the working view and the history are separate
places.

## Database state

96 `no_match` rows were deleted on 2026-09-07, with a backup taken first
(`data/backups/backup_2026-09-07_191255.zip`). The database now holds 55 jobs:
applied 6, matched 4, rejected 45, plus 55 match results, 10 cover letters and
14 runs. PROJECT_INSTRUCTIONS' "151 rows must survive" invariant was set aside
deliberately by the user, not broken by a refactor.

## Scope — in

1. **`src/ui/page.py` — replace the single `cards_container` with `ui.tabs` /
   `ui.tab_panels`.** Four tabs, in this order:

   | Tab | Contents |
   | --- | --- |
   | **Pipeline** (default) | Strong Matches, then Pending Unscored Postings |
   | **Applied** | Applied Jobs |
   | **Non-Matches** | Non-Matches |
   | **Rejected** | Rejected Matches |

   Pipeline opens by default. The header, Pipeline Status panel, Lifetime
   Metrics panel and both buttons stay above the tabs, always visible.

2. **Show the cover-letter date on applied cards, and sort newest first.**
   There is no `applied_at` column and no record of when Mark Applied was
   clicked. `letter_created_at` is the only available proxy and is already
   returned as a timezone-aware datetime (`service.py:231`). The user's stated
   workflow is to generate a letter for every application, so the proxy tracks
   closely going forward.

   - Sort `vs.applied` by `letter_created_at` descending in `build_view_state`,
     newest first. Entries with `letter_created_at` of `None` sort last.
   - Label it honestly on the card: **"Letter written: 2026-07-13"**, not
     "Applied". It is not the application date and must not claim to be.

   Shipped implementation:

       applied.sort(key=lambda c: c.letter_created_at or _EPOCH, reverse=True)
       applied.sort(key=lambda c: c.letter_created_at is None)

   Two passes, relying on sort stability. A single tuple key
   `(c.letter_created_at is None, c.letter_created_at or _EPOCH)` does not work
   at either setting: `reverse=False` sorts dates ascending, `reverse=True`
   moves `None` entries to the front.

3. **Rejected tab keeps the existing collapsed expansion panel** or becomes a
   plain list — the tab already provides the hiding the expansion was doing.
   Implementer's choice; state which and why.

## Closing out an application

No new status, no migration. `applied → rejected` is already an allowed
transition (`domain/models.py:32`) and the "Withdraw / Reject" button already
exists on applied cards (`page.py:529`, `:536`). Clicking it moves the job to
the Rejected tab.

Cost, accepted by the user: `rejected` means both "dismissed before applying" and
"applied, went nowhere". The distinction is lost. Separating them would need a
new status value, a CHECK-constraint migration at `user_version` 2, and a new
edge in `ALLOWED_TRANSITIONS`. Not worth it until there is a reason to measure
application-to-response rate.

## Scope — explicitly out

- Any schema change. No `applied_at`, no new status value, no migration.
- `domain/models.py` and `ALLOWED_TRANSITIONS`. Untouched.
- The 1-second refresh timer (`page.py:657`) and the `list_jobs_with_match`
  projection pulling every description (A5). Tabs neither help nor worsen this;
  NiceGUI builds all panels regardless of which is visible. Batch 8.
- `build_view_state`'s routing logic, beyond adding the sort. The status →
  bucket mapping at `:97-144` is correct and stays.
- `render_match_card`'s three modes. The card bodies do not change; only where
  they are placed, plus the date line on applied cards.
- The `location` normalization defect — `JobPosting.location` on the Unilabs row
  holds a stringified dict (`{'raw': 'Porto, Portugal', 'locality': 'Porto', …}`)
  which renders raw and overflows the card. Real, reproducible on future scrapes
  from that source, and an ingestion bug rather than a UI one. Backlog.
- Any bulk action. No "reject all".

## Constraints

- NiceGUI 3.13.0. `ui.tabs`, `ui.tab`, `ui.tab_panels`, `ui.tab_panel` are
  built in — no dependency added.
- `rebuild_cards` currently fires only when `cards_signature` changes
  (`page.py:649-654`). That throttle must survive the restructure or every card
  rebuilds every second.
- Switching tabs must not trigger a rebuild or lose scroll position.
- `generating_hashes` is checked inside `render_match_card` (`:475`) and folded
  into the signature at `:651`. Cover-letter generation must still update the
  right card after the restructure.

## Acceptance criteria

1. `py -m pytest -q` passes. `tests/test_ui_shell.py` asserts on view state and
   may need to know about the sort — if it fails, report it, do not edit it.
2. All four tabs render. Pipeline is selected on load.
3. The Applied tab shows 6 cards, ordered newest letter first.
4. Each applied card shows "Letter written: <date>", or nothing when the job has
   no letter.
5. Switching tabs does not rebuild cards — verify by generating a letter, then
   switching tabs and back, and confirming the card state persists.
6. "Withdraw / Reject" on an applied card moves it to the Rejected tab.
7. No code in `src/ui/` reads or writes an `applied_at` column, and nothing
   in `src/ui/` references `ALLOWED_TRANSITIONS`. Comments noting that the
   column does not exist are expected and are not violations.

## Rollback

`git checkout master`, delete the branch. No schema change, nothing to undo in
the database.