# Job Match Assistant

A local-first, single-user tool that scrapes job postings, scores them against
your CV with an LLM, drafts cover letters, and mirrors everything to a Google
Sheet. It runs on your machine, stores everything in a local SQLite file, and
keeps your CV and credentials out of version control.

The dashboard is a browser page served by NiceGUI. You click **Run Pipeline**,
it scrapes, scores, and shows you what is worth applying to.

---

## What you need before you start

| | Why |
| :--- | :--- |
| **Python 3.14** | What the project is developed and run on. `pyproject.toml` still declares `>=3.11` — see Known gaps. |
| **An Apify account, paid plan** | The job scraper is an Apify Actor. The free plan blocks it after a few runs — see Costs. |
| **An OpenAI or Google API key** | Scoring and letter-writing. One provider, your choice. |
| **A Google Cloud service account** | Optional. Only for the Google Sheet mirror; the pipeline runs fine without it. |

---

## Setup

### 1. Clone and install

```powershell
git clone <your fork url>
cd job-match-assistant
py -m pip install -r requirements.txt
```

### 2. Configure

```powershell
Copy-Item .env.example .env
```

Then edit `.env`. These are the keys you must set:

| Key | What to put | Where to get it |
| :--- | :--- | :--- |
| `SCRAPER_SOURCE` | The adapter name matching your Actor's output shape: `apify_linkedin`, `apify_indeed`, or `agentx_all_jobs`. | The registry is in `src/ingestion/normalization.py`. |
| `APIFY_API_TOKEN` | Your Apify token. | Apify Console → Settings → Integrations → API tokens. |
| `APIFY_ACTOR_ID` | The Actor to run. | Apify Store — the ID in the Actor's URL. |
| `LLM_PROVIDER` | `openai` or `google`. | — |
| `LLM_MODEL` | Model name for that provider. | — |
| `LLM_API_KEY` | Key for whichever provider you chose. | OpenAI platform, or Google AI Studio. |
| `SCRAPER_QUERY` | Job titles to search for, comma-separated — up to `SCRAPER_MAX_QUERIES` (default 5). Each title is a separate Apify Actor Start. | — |
| `SCRAPER_LOCATION` | Where. | — |
| `SCRAPER_LIMIT` | Results per run. Start small. | — |
| `SCORE_THRESHOLD` | Score at or above which a job counts as a match. | See Known gaps — the default of 70 may be high. |
| `CANDIDATE_NAME` | Your name as it should appear at the top of an exported cover letter. Optional. | — |

Every key is documented in [CONFIG.md](CONFIG.md).

### 3. Fill in the three knowledge files

**This is the step people skip, and nothing works until it is done.**

```powershell
Copy-Item data\knowledge\cv.md.example           data\knowledge\cv.md
Copy-Item data\knowledge\persona.md.example      data\knowledge\persona.md
Copy-Item data\knowledge\ats_criteria.md.example data\knowledge\ats_criteria.md
```

Then edit each and replace every `<placeholder>`. The app logs a warning at
startup if placeholders remain, and refuses to start if any of the three is
missing or empty.

| File | What it is |
| :--- | :--- |
| `cv.md` | Your CV. The scorer measures postings against it, and the writer may only make claims that appear here. |
| `persona.md` | Voice, tone, length and structure for cover letters. Nothing about job preferences. |
| `ats_criteria.md` | How jobs are judged. The most important file — see [How scoring works](#how-scoring-works). |

### 4. Google Sheets mirror (optional)

Skip this if you do not want the mirror. Leave `LEDGER_SHEET_ID` blank and the
pipeline runs normally: each run logs one warning that the sync failed and
carries on. The run is still recorded as successful.

To enable it:

1. Create a project in the Google Cloud Console.
2. Enable the **Google Sheets API**.
3. Create a **service account**, then create a JSON key for it.
4. Save the key as `secrets/service_account.json`.
5. Create a Google Sheet.
6. Share the sheet with the service account's email address, as **Editor**.
   This is the step that is easy to miss — without it you get a permissions
   error that names the service account.
7. Copy the sheet ID from its URL — the part between `/d/` and `/edit` — into
   `LEDGER_SHEET_ID` in `.env`.

The app only ever writes to the sheet. It never reads state back, and any
columns you add yourself are preserved.

### 5. Verify

```powershell
py -m pytest -q
```

**A clean clone may fail here.** The suite reads the real `.env` from the working
directory rather than using synthetic settings, so `SCRAPER_SOURCE` and
`LLM_API_KEY` must already be set or collection fails. If you completed step 2 it
should pass. This is a known defect — see Known gaps.

### 6. Run it

```powershell
py src\ui\main.py
```

The browser opens on its own at http://127.0.0.1:8080.

For a desktop shortcut, run this once:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1
```

That creates Desktop and Start Menu shortcuts. Closing the browser tab shuts the
app down after a few seconds; refreshing the page does not.

---

## How scoring works

Every posting is judged by `data/knowledge/ats_criteria.md`, which you write. It
has two parts.

**Hard gates, evaluated first.** Binary knockouts — a required language you do
not speak, an on-site role in a city you will not travel to, a stated minimum
above your years of experience. One failure means every dimension is zero and
the job scores zero. A gate only fires on evidence stated in the posting;
silence is not failure.

**Five weighted dimensions, each scored 0–100 independently.** The model returns
dimension values only. **The score is computed in code** as the weighted sum,
using `DIMENSION_WEIGHTS` in `src/skills/scorer.py`. The model used to be asked
for the sum and got it wrong by up to 16 points in both directions, so the
arithmetic moved out of the prompt.

Two things to know before writing your own criteria:

- **Renaming a dimension means editing `DIMENSION_WEIGHTS` too.** The scorer
  validates that the model returned exactly the five expected keys and rejects
  the posting otherwise. The names must match on both sides.
- **A dimension that measures your CV rather than the posting is wasted.** Your
  CV is the same document on every job, so such a dimension returns the same
  value every time and carries no information. The original criteria file had
  four such dimensions out of four, and 70% of the weight was a constant. Write
  dimensions that measure the *posting*, or the posting *against* the CV. The
  full analysis is in
  [docs/plans/batch-05-criteria-rewrite.md](docs/plans/batch-05-criteria-rewrite.md).

---

## Costs

Two meters run at once.

**Apify** bills per Actor Start plus per result. A run returning about 39 results
cost roughly **$0.15**.

**The LLM** bills per token — roughly 4,900 input tokens per job, because every
posting is scored against the whole CV and the whole criteria file. On Gemini 2.5
Flash at its paid-tier list price ($0.30 per million input tokens, $2.50 per
million output, September 2026) that is about **$0.002–0.003 per job**, and the
same run cost about **$0.01**.

Each run's estimated cost is recorded in the `runs` table and shown in the app.
**It is only as accurate as `LLM_INPUT_TOKEN_RATE_USD` and
`LLM_OUTPUT_TOKEN_RATE_USD`.** They default to GPT-4o prices and must be set to
your provider's rates, or the recorded cost is meaningless. Runs recorded with the
GPT-4o defaults against Gemini 2.5 Flash overstate the LLM cost about twelve
times. The Apify component currently reports as zero; only LLM tokens are counted.

**The free Apify plan will not carry you far.** It blocks the Actor after a small
number of runs, and the failure is silent: the run completes successfully and
returns zero items. If your scrapes suddenly return nothing with settings that
worked an hour earlier, check your Apify plan before debugging anything else.

To develop without spending anything, set `REPLAY_FROM_CACHE=True`. Every live
scrape is written to `data/raw_scrapes/` first, and replay reads the most recent
file instead of calling Apify. LLM calls still cost money.

---

## Using it

The dashboard has four tabs.

| Tab | What is in it |
| :--- | :--- |
| **Pipeline** | Jobs at or above `SCORE_THRESHOLD`, plus anything scraped but not yet scored. The working view. |
| **Applied** | Jobs you have marked applied, newest cover letter first. |
| **Non-Matches** | Below threshold. Compact list, scores only. |
| **Rejected** | Dismissed, or applied and closed out. |

On a matched card you can **Write Letter**, **Regenerate**, **Mark Applied**, or
**Reject**. **View** opens the letter in an editable dialog: **Save** keeps your
edits, **Copy** copies the text as shown, and **PDF** downloads it — including any
edits you have not saved yet.

PDF export uses the PDF format's built-in Helvetica font, which covers Latin-1
(Western European) characters only. Curly apostrophes and en-dashes are converted
to their plain equivalents; any other character outside Latin-1 — a Euro sign, a
Polish or Turkish name — stops the export with a message naming the character. The
name at the top of the PDF comes from `CANDIDATE_NAME` in `.env`.

Closing out an application uses the same **Withdraw / Reject** button on an
applied card. There is no separate "closed" status — `rejected` means both
"dismissed before applying" and "applied, went nowhere".

`no_match` is terminal. A job scored below threshold is never re-scored, so
lowering `SCORE_THRESHOLD` does not revive anything already rejected.

---

## Backups

```powershell
py scripts\backup.py [--output-dir data\backups] [--keep N]
```

- **`--output-dir`** — where archives are written. Defaults to `data/backups/`.
- **`--keep`** — how many to retain. Defaults to `10`.

Safe to run while the app is running; it takes a consistent online snapshot of
the database. The archive is timestamped, e.g. `backup_2026-06-14_153045.zip`,
and contains `app.db`, `cv.md`, `persona.md`, `ats_criteria.md`, `.env`, and
`service_account.json`.

> **The archive contains your API keys and your service account private key in
> cleartext.** `data/backups/` is gitignored, but an archive moved elsewhere is
> no longer protected. Do not commit one, and think before putting one in a
> synced folder.

To restore: stop the app, extract the archive, and put the files back — `app.db`
into `data/`, the three markdown files into `data/knowledge/`, `.env` into the
repository root, `service_account.json` into `secrets/`.

---

## What is not in this repository

A fresh clone is deliberately empty of anything personal. These are gitignored:

- `.env` — your API keys
- `secrets/` — your service account key
- `data/app.db` — every job, score and letter
- `data/knowledge/cv.md`, `persona.md`, `ats_criteria.md` — your CV and criteria
- `data/raw_scrapes/`, `data/backups/`, and the rotating logs

The `.example` files are tracked; the live ones are not. That is why setup step 3
exists.

---

## Known gaps

An honest list.

- **The test suite reads your real `.env`.** There is no `tests/conftest.py`, so
  `pytest` picks up whatever is in the working directory. A clean clone with no
  `.env` fails at collection. Audit item C5.
- **`requires-python` says `>=3.11`, the code runs on 3.14.** Nothing verifies
  that 3.11 works. Treat 3.14 as the real floor.
- **`SCORE_THRESHOLD` defaults to 70.** Whether that is right depends entirely on
  your criteria file. Score a batch first, look at the spread, then decide.
- **Location is part of a job's identity hash — including latitude and
  longitude.** A posting re-scraped with a slightly different coordinate is
  treated as a new job and re-scored at full LLM cost. Deduplication fails
  silently.
- **`LEDGER_XLSX_PATH` is dead configuration.** Nothing writes an xlsx file; the
  ledger is Google Sheets. Audit item B9.
- **The Apify compute cost always reports as zero.** The scraper reads the
  compute units but never passes them to the cost accumulator, so
  `cost_estimated_usd` covers LLM tokens only. Audit item B2.

---

## Project documents

The reasoning behind the code lives in [docs/](docs/). The four project documents
each open with a status header saying what still holds and what has moved — read
that before the body.

| Document | What it is for |
| :--- | :--- |
| [PROJECT_INSTRUCTIONS.md](docs/PROJECT_INSTRUCTIONS.md) | The rules the work is done under: no fabrication, ask before adding a dependency, what must not break. |
| [ATS_DOMAIN_BRIEF.md](docs/ATS_DOMAIN_BRIEF.md) | How postings are judged: hard gates, fit dimensions, calibration. |
| [WORK_ORDER.md](docs/WORK_ORDER.md) | The batches of work planned from the audit, with their status. |
| [DECISIONS.md](docs/DECISIONS.md) | Architectural decisions D1–D10: what was decided, the cost, and what would reverse it. |
| [HANDOFF.md](docs/HANDOFF.md) | The original architecture audit and the finding IDs (A, B, C) that plans and commits cite. |
| [plans/](docs/plans/) | One implementation plan per change: intent, scope, decisions, acceptance criteria, backlog. |
