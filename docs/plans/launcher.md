# Implementation Plan — Desktop Launcher

**Branch:** `feat/desktop-launcher` · **Base:** `refactor/single-ui` @ `1365977`
**Not in the work order.** User-requested. Depends on batch 3.

## Intent

A desktop shortcut that starts the app, opens the browser, and leaves no orphaned
Python process when the user is done with it.

## What is actually wrong today

1. `src/ui/main.py:27-32` calls `ui.run()` without `reload`. NiceGUI defaults
   `reload=True`, which spawns a uvicorn reloader child process — hence the
   `{"__main__", "__mp_main__"}` guard at line 34. Two `python.exe` processes
   exist at runtime. Killing the parent orphans the child. This is the primary
   cause of the reported problem, and no shutdown handler addresses it.
2. No lifecycle hook is registered anywhere in `src/`. Closing the browser tab
   leaves the server running indefinitely.
3. `RunCoordinator._execute_run` (`src/coordinator/run_coordinator.py:145`)
   catches `Exception`, which does not include `asyncio.CancelledError`
   (a `BaseException` subclass since 3.8). On cancellation, control reaches the
   `finally` at :150, where `if run.status == RunStatus.RUNNING` sets the run to
   **`DONE`**. A cancelled run currently records itself as successful.
4. That same `finally` awaits `save_run(...)` while the task is cancelling. The
   write may never complete.

## Shutdown policy — decided

**On shutdown, an in-flight run is cancelled and its `runs` row is written as
`failed` with `finished_at` stamped, before the process exits.** Roughly one
second.

Rejected: blocking until the run completes (30–90s hang reads as a freeze);
cancelling without a write (`init_db:108` would repair the row on next launch,
but crash recovery is a safety net, not a shutdown strategy).

Because of defects 3 and 4 above, `shutdown()` must not delegate the status write
to `_execute_run`'s `finally`. Order is: cancel → await (suppressing
`CancelledError`) → write `failed` from the shutdown coroutine. Last write wins.

Jobs already scored keep their scores. Jobs not reached stay `scraped` and are
re-scored next run — the recovery path working as designed (D6), not data loss.
In-flight LLM calls are paid for and discarded.

## What counts as "closing the app"

The browser tab is the app window. Closing it triggers `app.on_disconnect`,
which starts a grace timer; if no client reconnects within `UI_EXIT_GRACE_SECONDS`
(default 5), the server shuts down via NiceGUI's own path, which fires
`app.on_shutdown` handlers cleanly.

The grace period exists because a page refresh disconnects and reconnects. Without
it, F5 would kill the app.

The console window remains a backup kill switch: closing it terminates the process
tree. With `reload=False` there is only one process to terminate.

## Scope — in

1. **`src/ui/main.py`** — `ui.run(..., reload=False, show=True)`. `reload=False`
   is the single most important line in this batch. `show=True` replaces the
   current `show=False` at :30 so the launcher opens a browser.
2. **`RunCoordinator.shutdown()`** — new public coroutine on the coordinator:
   - returns immediately if `_active_task` is None or already done
   - cancels the task, awaits it with `contextlib.suppress(asyncio.CancelledError)`
   - sets `status = failed`, stamps `finished_at`, and persists via the existing
     `save_run` path
   - never raises; any failure is logged and swallowed
   - is idempotent — calling it twice is harmless
3. **`RunCoordinator._execute_run`** — add an `except asyncio.CancelledError`
   branch before the existing `except Exception` (:145) that sets
   `run.status = RunStatus.FAILED` and re-raises. Without this, defect 3 stands.
4. **`src/ui/main.py`** — register `app.on_disconnect` (grace-period shutdown
   trigger) and `app.on_shutdown` (calls `await coordinator.shutdown()`).
   `src/ui/main.py` must call the public `shutdown()`; it must not touch
   `coordinator._active_task`. A3 is already a standing lesson about reaching
   into private attributes.
5. **`scripts/launch.bat`** — new. Resolves the repo root from its own location
   (`%~dp0..`), never from the caller's working directory. Invokes `py`. Must
   work when launched from a shortcut whose working directory is elsewhere.
6. **`scripts/install_shortcut.ps1`** — new, run once by the user. Creates a
   Start Menu and Desktop shortcut to `launch.bat`. Uses `assets\jma.ico` if
   present; otherwise falls back to a system icon. No image dependency is added.
7. **`UI_EXIT_GRACE_SECONDS`** — new setting in `src/config/settings.py`,
   default 5, added to `.env.example` and `CONFIG.md`. See "Config note" below.

## Scope — explicitly out

- `sync_ledger()`'s `from nicegui import ui` (A2) — batch 6. The shutdown work is
  not a pretext to start injecting a notification callback.
- `lock=self.persistence_service._write_lock` (:51, A3) — batch 6.
- `get_active_run()`'s side-effecting mutation (B10) — batch 6.
- `compute_units=0.0` (:109, B2) — batch 4.
- `country=... or "Portugal"` (:68, C8) — batch 8.
- `ledger_xlsx_path` (B9) — batch 8.
- `src/ui/page.py:2`'s unused `Any` import — batch 8.
- The `ui.timer(1.0, refresh)` at `page.py:657` and the projection it drives
  (A5) — batch 8.
- Any schema change. `runs.status` already accepts `failed`.
- Packaging, PyInstaller, autostart, service installation, native/pywebview mode.
- Adding any third-party dependency.

## Config note

`UI_EXIT_GRACE_SECONDS` is a new setting, which brushes against batch 2 and C7.
It is added here because the shutdown behaviour is not tunable otherwise and a
hardcoded 5 would be exactly the kind of buried default C8 exists to complain
about. `CONFIG.md` is updated for this key only; the six keys C7 says are missing
stay missing until batch 8.

## Constraints

- Windows PowerShell **5.1**. `Select-String` has no `-Recurse`; use
  `Get-ChildItem -Recurse | Select-String`. A `-Recurse` call errors and can be
  mistaken for an empty (passing) result.
- Python is `py`. No virtualenv; global site-packages at
  `C:\Users\USER\AppData\Local\Python\pythoncore-3.14-64\python.exe`.
- NiceGUI 3.13.0. `app.on_startup`, `on_shutdown`, `on_connect`, `on_disconnect`
  all exist. Verify signatures and coroutine support before use — do not assume.
- `scripts/backup.py` hardcodes `env_path="."`-relative. Do not repeat that class
  of bug: the launcher must resolve paths from its own location.
- No shutdown path may raise. A failure to record `failed` is logged; the process
  still exits.

## Acceptance criteria

Generated artefacts (`*.egg-info/`, `__pycache__/`, `.pytest_cache/`) are excluded
from all greps.

1. `py -m pytest -q` passes.
2. With the app running, `Get-Process python*` shows exactly **one** process.
   (Today it shows two. This is the `reload=False` proof.)
3. Double-clicking the shortcut opens the dashboard in a browser.
4. Closing the browser tab with **no run active**: within the grace period,
   `Get-Process python*` returns nothing.
5. Closing the browser tab **during a run**: the process exits, and — queried
   **before relaunching the app** — the run's row reads `status='failed'` with
   a non-null `finished_at`.

   Exit is immediate when the run is closed mid-scoring. When closed during an
   active Apify scrape, exit is delayed until the scrape thread returns:
   ApifyScraperClient.fetch is synchronous (apify_client.py:42-64), runs via
   asyncio.to_thread (ingestion/service.py:28), and passes no timeout to
   .call() (apify_client.py:49-51). Cancellation unwinds the coroutine at once
   but cannot interrupt the thread, and loop teardown joins the default
   executor. Allow up to 3 minutes in that case. This is pre-existing
   behaviour, not introduced by this branch.

   Relaunching before running the verification query would let init_db:108's
   crash-recovery sweep disguise a broken handler as a working one.
6. Refreshing the browser tab (F5) does **not** shut the app down.
7. `Get-ChildItem .\src -Recurse -Filter *.py | Select-String -Pattern "_active_task"`
   matches only inside `src/coordinator/run_coordinator.py`.

Criterion 5 verification query, run before relaunch:

    py -c "import sqlite3;c=sqlite3.connect('file:data/app.db?mode=ro',uri=True);[print(r) for r in c.execute('select run_id,status,started_at,finished_at from runs order by started_at desc limit 3')]"

## Decision log entry to add on merge

    ## D10 — Closing the browser tab shuts the app down; an in-flight run is
    cancelled and recorded as failed.
    **<date>**

    ui.run() defaulted to reload=True, spawning a uvicorn reloader child, so the
    app ran as two processes and killing one orphaned the other
    (src/ui/main.py:27). No lifecycle hook existed anywhere in src/. And
    _execute_run's except Exception (run_coordinator.py:145) does not catch
    CancelledError, so a cancelled run recorded itself as done.

    **Decision:** reload=False; app.on_disconnect with a grace period triggers
    shutdown; RunCoordinator.shutdown() cancels the task and writes failed
    itself rather than trusting _execute_run's finally.
    **Cost:** loses auto-reload during development. Restart to pick up changes.
    **Would reverse it:** switching to NiceGUI native mode, which changes the
    window-ownership model entirely.

## Observed behaviour, verified manually 2026-09-02

Criteria 1, 2, 3, 4, 6 and 7 pass. Criterion 5 passes with the delay
described above.

On a run cancelled mid-scrape, the `runs` row is written by `_execute_run`'s
`finally` (run_coordinator.py:169), not by `shutdown()`. The new
`except asyncio.CancelledError` branch sets FAILED before re-raising, so the
`finally` persists the correct status — but `n_errors` reads 0 rather than 1,
because `shutdown()`'s own write does not complete. Acquiring `_write_lock`
inside `shutdown()` is an await during teardown and can itself be cut short
(service.py:522-523 holds the lock across the to_thread call). The status is
correct; the error count is not incremented on that path. Accepted.

### Backlog, not this branch

- `ApifyScraperClient.fetch` takes no timeout. A bounded `.call()` would make
  shutdown prompt in all cases. Ingestion change, separate branch.
- `_exit_after_grace` is created with raw `asyncio.create_task`, so it is in
  neither uvicorn's `server_state.tasks` nor NiceGUI's
  `background_tasks.running_tasks`. Nothing awaits or cancels it; if the loop
  closes while it is suspended, Python emits "Task was destroyed but it is
  pending". Not observed.
- `src/ui/page.py:2` imports `Any` unused (batch 8).
- `src/ui/page.py` is 658 lines. The audit and the Project's decision log both
  record 657. Correct the figure wherever it is carried forward.

## Rollback

`git checkout refactor/single-ui`, delete the branch. No schema change, no
migration, nothing to undo in the database.