# Implementation Plan — Batch 3: One UI

**Branch:** `refactor/single-ui` · **Audit:** A1, A4 · **Decision:** D1
**Base:** `58b8cb2` (master) · **Status:** `git rm src/main.py` already staged

## Intent

Delete the Streamlit UI and every code path that exists only to serve it. NiceGUI
(`src/ui/`) is the only entry point. This removes the cross-event-loop `asyncio.Lock`
hazard (A4) by deletion rather than by fixing it in two places, and it is a
prerequisite for a desktop launcher with a single, reliable shutdown path.

## Scope — in

1. `src/main.py` — deleted (already staged).
2. `src/coordinator/run_coordinator.py`:
   - Remove the `except RuntimeError` → daemon-thread branch in `start_run()`
     (lines 87–92 of the pre-change file). Only the `loop.create_task` path remains.
   - Move the running-loop check so it precedes construction of the `Run` object.
     A failure must not leave a phantom `RUNNING` run in `self._active_run`.
     Raise `RuntimeError` with a message naming the required asyncio context.
   - `get_active_run()` (was lines 203–208): `_active_task` is now always an
     `asyncio.Task`. Collapse the two `isinstance` branches to a single
     `.done()` check.
   - `wait()` (was lines 215–219): remove the `hasattr(..., "join")` branch.
     Await the task directly.
   - Line 55: `self._active_task: Optional[Any]` becomes
     `Optional[asyncio.Task]`. See "Supersedes" below.
   - Remove `import threading` (line 2) — no remaining users.
3. `pyproject.toml` — remove the `"streamlit>=1.30.0",` dependency line.
4. `requirements.txt` — remove the `streamlit>=1.30.0` line.
5. `.agent/rules/architecture.md:7` — replace the Core Tech Stack line with:
   `- **Core Tech Stack**: Python 3.14, NiceGUI (single User Interface; the pipeline runs as an asyncio task on the UI's event loop), SQLite (System of Record).`

## Scope — explicitly out

Do not touch these in this branch, even though they are visible in the same files:

- `sync_ledger()`'s `from nicegui import ui` (A2) — batch 6.
- `lock=self.persistence_service._write_lock` at line 51 (A3) — batch 6.
- `get_active_run()` mutating state as a getter side effect (B10) — batch 6.
  Only the `isinstance` collapse is in scope here.
- `compute_units=0.0` at line 109 (B2) — batch 4.2.
- `country=settings.scraper_country or "Portugal"` (C8) — batch 8.
- `ledger_xlsx_path` in `src/config/settings.py:20,93` (B9) — batch 8.
  It loses its last reader with `src/main.py`, but removing validated config is a
  separate concern.
- `CONFIG.md:67`, which also names Streamlit (C7) — batch 8.
- Adding `openai`, `nicegui`, `google-genai` to the manifests, or pinning any
  version (C3) — batch 2. Only the streamlit line is removed here.
- Any change to `requires-python` — batch 2 item 4.
- Any test file. See "If the suite fails" below.

## Supersedes

**WORK_ORDER 4.1 is struck, not deferred.** It calls for importing `Any` to fix
the latent `NameError` at `run_coordinator.py:55` (audit B7). `Optional[Any]`
existed because `_active_task` was polymorphic — `asyncio.Task` or
`threading.Thread`. With the thread path deleted it has exactly one type, so the
correct annotation is `Optional[asyncio.Task]` and `Any` is not needed. B7 is
closed by this batch. Batch 4 must not re-add the import.

## Acceptance criteria

All five must hold before this branch is committed.

1. `Select-String -Path .\src\*,.\tests\* -Pattern "streamlit" -Recurse -CaseSensitive:$false`
   returns nothing.
2. `Select-String -Path .\pyproject.toml,.\requirements.txt -Pattern "streamlit" -CaseSensitive:$false`
   returns nothing.
3. `Select-String -Path .\src\coordinator\run_coordinator.py -Pattern "threading|\bAny\b"`
   returns nothing.
4. `py -c "import sys; sys.path.insert(0,'src'); import coordinator.run_coordinator; print('import ok')"`
   prints `import ok`.
5. `py -m pytest -q` passes.

## If the suite fails

The expected failure is a test calling `start_run()` from a synchronous context
with no running event loop — it previously got the daemon-thread path and now
raises.

**Do not edit or delete any test to make the suite pass.** Report the full
failure output and stop. Whether a failing test should grow an async context or
be deleted as a Streamlit-path assertion is an architectural decision, not an
implementation one.

## Rollback

`master` holds `58b8cb2` with `src/main.py` intact. Recover with
`git checkout master -- src/main.py`, or abandon the branch entirely with
`git checkout master; git branch -D refactor/single-ui`.