"""
Renders a cover letter as a single-column A4 PDF.

Pure: takes values, returns bytes. No database, no files, no settings.

Uses the PDF's built-in Helvetica, which fpdf2 encodes as Latin-1 by default. The two
typographic characters the writer model is known to insert are normalised to plain
equivalents; any other character outside Latin-1 raises LetterPdfError naming it,
rather than producing a corrupted or silently altered PDF.
"""
from __future__ import annotations

import re
from datetime import date, datetime

# Outside Latin-1, and carry no meaning worth keeping in a cover letter.
_NORMALISE = {
    "’": "'",  # right single quotation mark (curly apostrophe)
    "–": "-",  # en dash
}

# English regardless of locale. strftime("%B") follows the process locale, and the
# unpadded-day codes differ by platform ("%-d" raises on Windows).
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

# Geometry in millimetres: A4, 25 mm top and bottom, 20 mm left and right.
_MARGIN_TOP_BOTTOM = 25
_MARGIN_LEFT_RIGHT = 20

_FONT = "helvetica"
_NAME_SIZE = 13
_META_SIZE = 9
_BODY_SIZE = 11
_BODY_LINE_HEIGHT = 5.5    # mm, about 1.4x an 11 pt line
_PARAGRAPH_GAP = 3.5       # mm between paragraphs
_RULE_WIDTH = 0.18         # mm, 0.5 pt
_GREY = (100, 100, 100)
_BLACK = (0, 0, 0)

# Characters Windows forbids in a filename, plus control characters.
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FILENAME_PART_LIMIT = 60


class LetterPdfError(ValueError):
    """Raised when a letter cannot be rendered as a PDF."""
    pass


def format_letter_date(when: date) -> str:
    """'11 September 2026'."""
    return f"{when.day} {_MONTHS[when.month - 1]} {when.year}"


def normalise_text(text: str) -> str:
    """
    Replaces the known non-Latin-1 characters, then refuses anything else the
    built-in font cannot encode, naming the first offending character.
    """
    for source, replacement in _NORMALISE.items():
        text = text.replace(source, replacement)
    for index, ch in enumerate(text):
        if ord(ch) > 0xFF:
            raise LetterPdfError(
                f"The letter contains {ch!r} (U+{ord(ch):04X}) at character {index + 1}, "
                "which the PDF's built-in font cannot display. Replace it and export again."
            )
    return text


def split_blocks(text: str) -> list[list[str]]:
    """
    A blank line (whitespace-only counts) starts a new paragraph; a single newline is
    a line break within one. Returns paragraphs as lists of lines, with trailing
    whitespace removed and empty paragraphs dropped.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    for paragraph in re.split(r"\n[ \t]*\n", text):
        lines = [line.rstrip() for line in paragraph.strip("\n").split("\n")]
        if any(line.strip() for line in lines):
            blocks.append(lines)
    return blocks


def header_lines(candidate_name: str, company: str, when: date) -> list[tuple[str, str]]:
    """(text, role) pairs, role 'name' or 'meta'. A blank name or company is omitted."""
    lines = []
    if candidate_name.strip():
        lines.append((candidate_name.strip(), "name"))
    lines.append((format_letter_date(when), "meta"))
    if company.strip():
        lines.append((company.strip(), "meta"))
    return lines


def letter_pdf_filename(company: str, title: str) -> str:
    """
    'Cover Letter - <Company> - <Title>.pdf'. Keeps spaces, case and accents; removes
    only characters Windows forbids in filenames, collapses whitespace, and caps each
    part at 60 characters.
    """
    def clean(part: str) -> str:
        part = _UNSAFE_FILENAME_CHARS.sub(" ", part or "")
        part = re.sub(r"\s+", " ", part).strip()
        return part[:_FILENAME_PART_LIMIT].rstrip()

    parts = ["Cover Letter"] + [p for p in (clean(company), clean(title)) if p]
    return " - ".join(parts) + ".pdf"


def build_letter_pdf(
    letter_text: str,
    company: str,
    candidate_name: str,
    when: date | None = None,
) -> bytes:
    """
    Renders the letter and returns the PDF as bytes.

    when defaults to today's local date; tests inject it. All text is validated
    before fpdf2 is touched, so an unrenderable letter raises LetterPdfError.
    """
    if not (letter_text or "").strip():
        raise LetterPdfError("The letter is empty.")

    when = when or datetime.now().date()
    header = [
        (normalise_text(text), role)
        for text, role in header_lines(candidate_name or "", company or "", when)
    ]
    blocks = split_blocks(normalise_text(letter_text))

    # Imported here so the rest of the app, and every module importing ui.page,
    # loads without fpdf2; only export fails if the dependency is missing.
    try:
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos
    except ImportError as e:
        raise LetterPdfError(
            "PDF export needs the fpdf2 package: py -m pip install -r requirements.txt"
        ) from e

    pdf = FPDF(orientation="portrait", unit="mm", format="A4")
    pdf.set_margins(left=_MARGIN_LEFT_RIGHT, top=_MARGIN_TOP_BOTTOM, right=_MARGIN_LEFT_RIGHT)
    pdf.set_auto_page_break(auto=True, margin=_MARGIN_TOP_BOTTOM)
    pdf.add_page()

    for text, role in header:
        if role == "name":
            pdf.set_font(_FONT, style="B", size=_NAME_SIZE)
            pdf.set_text_color(*_BLACK)
            height = 7
        else:
            pdf.set_font(_FONT, size=_META_SIZE)
            pdf.set_text_color(*_GREY)
            height = 4.5
        pdf.cell(w=0, h=height, text=text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(2)
    pdf.set_draw_color(*_GREY)
    pdf.set_line_width(_RULE_WIDTH)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(8)

    pdf.set_font(_FONT, size=_BODY_SIZE)
    pdf.set_text_color(*_BLACK)
    for lines in blocks:
        # A prose paragraph is one line of text and is justified; fpdf2 leaves the
        # final line of a justified cell unstretched. A paragraph with hard line
        # breaks (a sign-off) is left-aligned, so none of its lines is stretched.
        align = "J" if len(lines) == 1 else "L"
        for line in lines:
            pdf.multi_cell(
                w=0, h=_BODY_LINE_HEIGHT, text=line, align=align,
                new_x=XPos.LMARGIN, new_y=YPos.NEXT,
            )
        pdf.ln(_PARAGRAPH_GAP)

    return bytes(pdf.output())
