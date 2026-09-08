# Implementation Plan — README and Manifest Repair

**Branch:** `docs/readme-and-manifests` · **Base:** `master` @ `406ae1d`
**Audit:** C3, C7

## Intent

Make a fork runnable. Today it is not: `requirements.txt` omits three packages the
app imports, `.env.example` omits four keys the app needs, and `README.md`
documents only the backup script.

## The blockers

**`requirements.txt` cannot install a working app.** It lists seven packages and
omits `nicegui`, `openai`, and `google-genai` — all imported by `src/`. The live
`.env` sets Google as the LLM provider, so `google-genai` is not optional.

**`pyproject.toml`** has `nicegui` and `openai` but not `google-genai`.

**`.env.example` and `CONFIG.md` omit four keys** that `settings.py` defines and
the app uses: `SCRAPER_COUNTRY`, `SCRAPER_POSTED_SINCE`,
`GOOGLE_SERVICE_ACCOUNT_PATH`, `LEDGER_SHEET_ID`.

**`CONFIG.md:67`** still says "NiceGUI/Streamlit UI server". Streamlit was deleted
in `1365977`.

A README instructing `pip install -r requirements.txt` on top of that would lie on
its first instruction. The manifests are fixed in this change.

## Scope — in

1. **`requirements.txt`** — add `nicegui`, `openai`, `google-genai`. Do not pin;
   pinning is batch 2's decision and needs a resolved dependency set.
2. **`pyproject.toml`** — add `google-genai` to `dependencies`, and raise the
   `nicegui` floor from `>=1.4.0` to `>=2.0.0` to match `requirements.txt`. The
   floor change is one line beyond this item as originally written: the code uses
   `ui.tabs`, `tab_panels`' `keep_alive`, and `Dialog.on_value_change`, so
   `>=1.4.0` lets pip resolve a version that cannot run the app. Divergent floors
   between the two manifests are worse than either value alone.
3. **`.env.example`** — add the four missing keys with commented explanations.
4. **`CONFIG.md`** — add the four missing keys; correct line 67 to name NiceGUI
   only; mark `LEDGER_XLSX_PATH` as dead config pending removal (B9).
5. **`README.md`** — full rewrite. Contents below.

## README contents

- **What it is.** Local-first, single-user. Scrapes postings, scores them against
  a CV with an LLM, drafts cover letters, mirrors to a Google Sheet.
- **Requirements.** Python 3.14 (see the floor note below), an Apify account with
  a paid plan, an OpenAI or Google API key, and — **optionally** — a Google Cloud
  service account.

  **The Google Sheet mirror is optional, not required.** `settings.py` validates
  nothing about it at boot: `google_service_account_path` and `ledger_sheet_id`
  both have defaults and no validator. `GoogleSheetLedger.__init__`
  (`ledger.py:112-122`) does pure assignment with no I/O, so it constructs
  successfully against a missing key file and an empty sheet ID. And
  `run_coordinator.py:140-144` sets `run.status = RunStatus.DONE` *before*
  calling `sync_ledger()`, then catches and logs the failure. A run with no
  Sheets configuration completes and is recorded as successful; only the mirror
  is skipped, with one warning per run. The README must say this rather than
  telling a forker the mirror is mandatory.
- **Setup, in order**, each step stating exactly which file to change and how:
  1. Clone, install from `requirements.txt`.
  2. Copy `.env.example` to `.env`. Table of every key that must change, its
     meaning, and where to obtain the value.
  3. Copy the three `data/knowledge/*.md.example` files, dropping `.example`.
     **The app will not score anything until these are filled in** — they are the
     candidate profile, the writing persona, and the evaluation criteria.
  4. Google Sheets setup: create a project, enable the Sheets API, create a
     service account, download the JSON key to `secrets/service_account.json`,
     create a sheet, share it with the service account email as Editor, take the
     sheet ID from the URL into `LEDGER_SHEET_ID`.
  5. `py -m pytest -q` to verify, noting that the suite currently reads the
     developer's `.env` from the working directory (C5), so a clean clone may
     fail until batch 2 lands. State this rather than let a forker hit it.
  6. `py src\ui\main.py`, or `scripts\install_shortcut.ps1` once and then the
     desktop shortcut.
- **How the three knowledge files work**, with emphasis on `ats_criteria.md`:
  gates evaluated first as binary knockouts, then five weighted dimensions each
  scored 0–100 independently, with the score computed in code from
  `DIMENSION_WEIGHTS` in `src/skills/scorer.py`. **A dimension that measures the
  CV rather than the posting returns the same value on every job and contributes
  nothing** — this is the finding from `docs/plans/batch-05-criteria-rewrite.md`
  and it is the single most useful thing to tell someone writing their own
  criteria. Note that renaming a dimension means editing `DIMENSION_WEIGHTS` too.
- **Costs.** Apify bills per Actor Start plus per result; the LLM bills per token.
  A run of ~39 results cost $0.15 on Apify plus ~$0.12 in LLM calls. State that
  the free Apify plan blocks this actor after a small number of runs, which
  presents as a successful run returning zero items.
- **Operating it.** Tabs, the score threshold, marking applied, closing out via
  Withdraw/Reject, `REPLAY_FROM_CACHE` for zero-cost offline runs.
- **Backups.** Keep the existing content, corrected: the archive contains secrets,
  so it must not be committed. `data/knowledge/*.bak` and `data/backups/` are
  gitignored.
- **What is not in the repo.** `.env`, `secrets/service_account.json`, `data/app.db`,
  and the three live `data/knowledge/*.md` files are all gitignored — deliberately,
  they hold personal data and credentials. A fork starts empty.
- **Known gaps**, linking to `docs/plans/`: the test suite reads the real `.env`
  (C5); `SCORE_THRESHOLD` defaults to 70 in `settings.py:47` while `.env.example`
  says 70 and a working setup may want lower; location is inside `identity_hash`
  including coordinates, so dedup fails silently on re-scrapes.

## Scope — explicitly out

- Pinning versions in either manifest. Batch 2.
- `requires-python`. Currently `>=3.11`; the code is developed and run on 3.14.
  State the discrepancy in the README as a known gap rather than changing it —
  the decision belongs to batch 2.
- Removing `LEDGER_XLSX_PATH` (B9). Documented as dead, not deleted.
- `tests/conftest.py` (C5). Documented as a known gap.
- Deleting root clutter (C4). Gitignored since the first commit and absent from
  a fork.
- Any application code. This change touches documentation and manifests only.

## Acceptance criteria

1. `py -m pytest -q` passes.
2. `Get-Content requirements.txt` contains `nicegui`, `openai`, `google-genai`.
3. `pyproject.toml` contains `google-genai`.
4. `.env.example` contains `SCRAPER_COUNTRY`, `SCRAPER_POSTED_SINCE`,
   `GOOGLE_SERVICE_ACCOUNT_PATH`, `LEDGER_SHEET_ID`.
5. Every key in `settings.py`'s `Settings` class appears in `.env.example`,
   commented or set, and in `CONFIG.md`. Verify by listing both and diffing
   against the class. `REPLAY_CACHE_PATH` and `LLM_BASE_URL` stay commented in
   `.env.example`: both are optional with `None` defaults, and a commented line
   documents the key while showing it is not required.
6. `Select-String -Path .\CONFIG.md,.\README.md -Pattern "Streamlit"` returns
   nothing.
7. No file under `src/` or `tests/` is modified.
8. `README.md` contains no personal data: no name, no email, no phone, no sheet
   ID, no API key, no employer names.

## Backlog

- **Multi-query scraping.** `RunCoordinator.start_run()` builds one
  `ScrapeQuery` from settings, so a run searches one title in one location.
  Making `SCRAPER_QUERY` and `SCRAPER_LOCATION` comma-separated lists, looping
  the actor call and pooling results before dedup, would cover more of what
  the user would actually apply to than raising `SCRAPER_LIMIT` on a single
  narrow search. Each query is a separate Actor Start, so it needs a budget
  cap in the same change — D6's `budget_cap` arriving early.
- **Manual job entry.** A form to paste a posting found outside the scraper —
  company, title, location, URL, description — constructing a `JobPosting`
  with `source="manual"`, upserting, and scoring it on the spot. Everything
  downstream is unchanged; the domain layer already supports it. Needs a
  single-job scoring entry point outside a run, which is a minimal form of
  D6's `score_jobs(hashes, budget_cap)`. `source` is NOT NULL with no CHECK
  constraint, so no migration.

## Rollback

`git checkout master`, delete the branch. Documentation only.