# Implementation Plan — Cover Letter PDF Export

**Branch:** `feat/letter-pdf` · **Base:** `master` @ `778fba7`
**Not in the work order.** User-requested. Completes the workflow: generate,
edit, export, send.

## Dependency decision

**`fpdf2` 2.8.8.** Adds two packages — `fpdf2` (pure Python) and `fonttools`
4.65.0 (prebuilt wheel for cp314 win_amd64). `defusedxml` and `Pillow` are
already installed. `requires-python >=3.10`.

**WeasyPrint was rejected.** It would add six packages *and* the GTK3 runtime,
a separate Windows installer from a GitHub releases page. It is present on this
machine only because an earlier project needed it — a fork would get a working
`pip install` and a non-working app. WeasyPrint earns its weight laying out a CV
with multi-column grids and page-break control; a cover letter is a page of
prose, and trading a system dependency for CSS we would not use is a bad trade.

PROJECT_INSTRUCTIONS requires asking before adding a dependency. Asked and
agreed.

**Imported lazily (D1).** `build_letter_pdf` imports `fpdf` inside the function.
Six existing test modules import `ui.page`; a top-level import would fail all of
them at collection, and fail app startup, on any machine without fpdf2. Lazily,
only the PDF button fails, with a message naming the missing package.

## API verification (step 0)

fpdf2 was installed first and every call was checked against the installed
2.8.8 source before the module was written. **No call differed from the plan.**
`FPDF(orientation, unit, format)`, `set_margins(left, top, right)`,
`set_auto_page_break(auto, margin)`, `add_page`, `set_font(family, style, size)`,
`set_text_color`, `set_draw_color`, `set_line_width`, `line(x1, y1, x2, y2)`,
`cell` and `multi_cell` with `text=`, `align`, `new_x`/`new_y` (`XPos.LMARGIN`,
`YPos.NEXT`), the `l_margin`/`r_margin`/`w` attributes, `get_y`, `ln`, and
`output()` returning a `bytearray` all match.

Two premises turned out to be less than the plan assumed:

- **Latin-1 is fpdf2's default for core fonts, not a hard limit.**
  `core_fonts_encoding = "latin-1"` (`fpdf/fpdf.py:396`), and the `set_font`
  docstring documents switching it to `cp1252` (`fpdf.py:2613-2615`). An
  unencodable character raises `FPDFUnicodeEncodingException`
  (`fpdf.py:5898-5909`) — probed with `’`, `–`, `€` and `ş`. This batch keeps
  Latin-1; see Backlog.
- **fpdf2 already refuses to stretch forced lines.** With justified alignment,
  its line breaker renders a line ending in a manual break, and the final line,
  left-aligned (`fpdf/line_break.py:834`, `:872`, `:890`). D3 below is therefore
  belt-and-braces rather than a fix fpdf2 needed.

## Font and encoding

**fpdf2's built-in Helvetica, with two characters normalised.**

The 17 stored letters contain exactly five distinct non-ASCII characters:

| Char | Codepoint | Count | Latin-1? |
| --- | --- | --- | --- |
| `²` | U+00B2 | 22 | yes |
| `'` | U+2019 | 7 | **no** |
| `ö` | U+00F6 | 3 | yes |
| `ã` | U+00E3 | 1 | yes |
| `–` | U+2013 | 1 | **no** |

fpdf2's core fonts use Latin-1 by default (verified above). `PM²`, `São Paulo` and `ö`
all work. The two that do not are typographic niceties the model inserted: a
curly apostrophe and an en-dash. They are normalised to `'` and `-`, which costs
nothing in a cover letter.

**Embedding a TrueType font was rejected.** DejaVu Sans is not on this machine,
so it would be downloaded and committed — roughly 700 KB of binary in the repo —
to solve a problem that two character substitutions solve.

**Unmappable characters are reported, not swallowed.** After normalisation, any
character that cannot be encoded to Latin-1 raises with the character and its
codepoint named. The check runs before fpdf2 is touched, so fpdf2's own exception
is never reached. A future letter containing a `€`, a Turkish name, or a Greek
letter fails visibly instead of producing a corrupted PDF. This is the one real
risk of the built-in-font approach and it is made loud rather than mitigated
away.

## Layout

Header, then the letter body. Nothing else — no recipient address block, no
sender address. A cover letter sent as an emailed PDF does not need them.

```
<CANDIDATE_NAME>                         <- bold, 13pt
11 September 2026                        <- export date, 9pt, grey
<Company>                                <- job.company, 9pt, grey
-------------------------------------    <- 0.5pt rule
                                         (blank)
<letter body, 11pt>
```

**Date format is English** — `11 September 2026` — regardless of the letter's
language. Decided by the user. Built from an explicit month table, not
`strftime`: `%B` follows the process locale, and `%-d` raises on Windows.

**A4, 25mm top and bottom margins, 20mm left and right**, matching the
`career-alchemist` letter geometry, which is conventional for European business
correspondence.

**Paragraph handling.** The `career-alchemist` implementation
(`pdf_exporter.py:149-153`) splits only on `\n\n`, so a single newline collapses
into a space — a sign-off written as `Kind regards,\nName` is merged into one
line. Here a blank line starts a new paragraph and a single newline is a line
break within one.

**Alignment (D3).** A single-line paragraph — ordinary prose — is justified. A
paragraph containing hard line breaks, such as a sign-off, is left-aligned, so a
short line is never stretched across the page. Neither implementation actually
stretches such a line: CSS does not justify the last line of a block, and fpdf2
left-aligns forced lines itself (see API verification). The defect
`career-alchemist` has is the merged sign-off above, not a stretched one. D3 is
kept as an explicit guarantee that does not depend on fpdf2's line breaker.

## Candidate name

**New setting `CANDIDATE_NAME` in `.env`**, gitignored, so no personal data
enters the repo. Added to `.env.example`, `CONFIG.md` and the README setup table.

Parsing the name from `cv.md`'s first heading was rejected: the CV is free-form
markdown with no guaranteed structure and a parser would break on the first
reformat. The cost of the setting is that it can drift from the CV — visible on
the first PDF generated, and cheap to fix.

If `CANDIDATE_NAME` is unset, the header shows the date and company only. The
export still works.

## Scope — in

1. **`src/export/letter_pdf.py`** — new module, new package.
   `build_letter_pdf(letter_text, company, candidate_name, when=None) -> bytes`.
   Pure: takes values, returns bytes, touches no database, writes no file. `when`
   defaults to today's local date and is injected by tests. Imports fpdf2 lazily
   (D1). Also `format_letter_date`, `normalise_text`, `split_blocks`,
   `header_lines` and `letter_pdf_filename`, each testable without fpdf2.

2. **`src/config/settings.py`** — `candidate_name: str = ""`
   (`CANDIDATE_NAME`).

3. **`src/ui/page.py`** — a **PDF** button in the letter dialog beside Copy,
   Close and Save. It exports **the textarea's current contents**, not the stored
   letter, so an unsaved edit exports as shown. Exporting never saves. When the
   textarea differs from the stored letter the notification says so: "PDF
   downloaded with unsaved edits. They are not saved yet — press Save to keep
   them." Delivered via `ui.download.content`.
   **Filename (D2):** `Cover Letter - <Company> - <Title>.pdf`. Keeps spaces,
   case and accents; removes only characters Windows forbids in filenames;
   collapses whitespace; caps each part at 60 characters.

4. **`requirements.txt` and `pyproject.toml`** — `fpdf2>=2.8.0`, unpinned,
   matching how every other dependency is declared.

5. **`.env.example`, `CONFIG.md`, `README.md`** — `CANDIDATE_NAME`, and a note in
   the README that PDF export uses built-in fonts and normalises curly quotes and
   en-dashes. README's out-of-date description of the letter modal ("a Copy
   button") is corrected to describe edit, Save, Copy and PDF (D5).

## Scope — explicitly out

- CV export. CV tailoring was ruled out; this exports letters only.
- Embedding a TrueType font, or any bundled asset.
- Writing PDFs to disk. Bytes to the browser, as `career-alchemist` does.
- Batch export.
- Recipient address blocks, letterheads, logos, signatures.
- Any schema change. Nothing about a PDF is persisted.
- `save_cover_letter`, `update_cover_letter`, `get_latest_cover_letter`.
- `n_errors`, the token rates, `settings.py:50` (`score_threshold`),
  `conftest.py`, `identity_hash`.

## Acceptance criteria

1. `py -m pytest -q` passes. No existing test modified.
2. New tests cover: the output starts with `%PDF`; the curly apostrophe and
   en-dash are normalised; a genuinely unmappable character raises naming the
   character and codepoint; a blank line makes a paragraph and a single newline
   makes a line break; an empty `candidate_name` omits the name line; the date
   renders in English format; an empty letter raises. Also: CRLF and
   whitespace-only lines split correctly; the filename keeps accents and removes
   forbidden characters; a letter longer than a page builds.
3. `Select-String -Path .\requirements.txt,.\pyproject.toml -Pattern "fpdf2"`
   matches both.
4. Manual: the PDF button appears in the letter dialog and downloads a file.
5. Manual: the PDF opens, shows name, date and company above a rule, and the
   letter below it, with paragraphs intact.
6. Manual: `PM²`, `São Paulo` and `ö` render correctly.
7. Manual: editing the textarea without saving, then exporting, produces the
   edited text.

## Backlog

- Only Latin-1 is supported. A letter containing a Euro sign, a Turkish or Polish
  name, or any character outside Latin-1 will raise rather than export. Two fixes
  exist. The cheaper one: fpdf2 documents `core_fonts_encoding = "cp1252"`
  (`fpdf.py:2613-2615`), which would render curly quotes, en-dashes and `€`
  natively with no font file — it still excludes Turkish, Polish and Greek. The
  complete one is embedding a TrueType font, ~700 KB of binary in the repo.
  Revisit if it ever actually blocks an application.
- No CV export. Reconsider only if CV tailoring is reconsidered, and that was
  ruled out because a tailored CV asserting invented dates or employers is
  discovered at interview.

## Rollback

`git checkout master`, delete the branch, `pip uninstall fpdf2 fonttools`. No
schema change, nothing persisted.
