from datetime import date

import pytest

from export import (
    LetterPdfError,
    build_letter_pdf,
    format_letter_date,
    header_lines,
    letter_pdf_filename,
    normalise_text,
    split_blocks,
)


WHEN = date(2026, 9, 11)

SAMPLE_LETTER = (
    "Dear Hiring Manager,\n\n"
    "I hold a PM² certification and have led delivery at Köln and São Paulo’s "
    "team – across three product lines.\n\n"
    "Kind regards,\nTest Name"
)


# --- format_letter_date ---

@pytest.mark.parametrize("when, expected", [
    (date(2026, 9, 11), "11 September 2026"),
    (date(2026, 9, 5), "5 September 2026"),
    (date(2027, 1, 1), "1 January 2027"),
])
def test_format_letter_date_is_english_without_leading_zero(when, expected):
    assert format_letter_date(when) == expected


# --- normalise_text ---

def test_normalise_replaces_curly_apostrophe_and_en_dash():
    assert normalise_text("it’s 2020–2026") == "it's 2020-2026"


def test_normalise_keeps_latin1_characters():
    assert normalise_text("PM², São Paulo, Köln") == "PM², São Paulo, Köln"


@pytest.mark.parametrize("text, ch, codepoint, position", [
    ("Salary €50k", "€", "U+20AC", 8),
    ("Baş", "ş", "U+015F", 3),
])
def test_normalise_raises_naming_the_unmappable_character(text, ch, codepoint, position):
    with pytest.raises(LetterPdfError) as exc_info:
        normalise_text(text)
    message = str(exc_info.value)
    assert repr(ch) in message
    assert codepoint in message
    assert f"character {position}" in message


# --- split_blocks ---

def test_blank_line_starts_paragraph_and_single_newline_is_a_line_break():
    text = "First paragraph.\n\nKind regards,\nTest Name"
    assert split_blocks(text) == [["First paragraph."], ["Kind regards,", "Test Name"]]


def test_crlf_line_endings_split_the_same_way():
    assert split_blocks("One.\r\n\r\nTwo\r\nThree") == [["One."], ["Two", "Three"]]


def test_whitespace_only_line_counts_as_blank():
    assert split_blocks("One.\n   \t\nTwo.") == [["One."], ["Two."]]


def test_extra_newlines_do_not_create_empty_paragraphs():
    assert split_blocks("\n\nOne.\n\n\n\nTwo.\n\n") == [["One."], ["Two."]]


def test_trailing_whitespace_is_removed_from_lines():
    assert split_blocks("Line one.   \nLine two.") == [["Line one.", "Line two."]]


# --- header_lines ---

def test_header_with_name_date_and_company():
    assert header_lines("Test Name", "Acme", WHEN) == [
        ("Test Name", "name"),
        ("11 September 2026", "meta"),
        ("Acme", "meta"),
    ]


def test_blank_candidate_name_omits_the_name_line():
    assert header_lines("   ", "Acme", WHEN) == [("11 September 2026", "meta"), ("Acme", "meta")]


def test_blank_company_omits_the_company_line():
    assert header_lines("Test Name", "", WHEN) == [("Test Name", "name"), ("11 September 2026", "meta")]


# --- letter_pdf_filename ---

def test_filename_removes_forbidden_characters_and_keeps_spaces():
    assert letter_pdf_filename("Acme: Labs/Portugal", "Project Manager?") == (
        "Cover Letter - Acme Labs Portugal - Project Manager.pdf"
    )


def test_filename_keeps_case_accents_and_inner_dots():
    assert letter_pdf_filename("São Paulo & Filhos Lda.", "Gestor de Projetos") == (
        "Cover Letter - São Paulo & Filhos Lda. - Gestor de Projetos.pdf"
    )


def test_filename_omits_a_blank_part_and_caps_long_parts():
    assert letter_pdf_filename("Acme", "") == "Cover Letter - Acme.pdf"
    long_name = letter_pdf_filename("A" * 100, "Title")
    assert long_name == f"Cover Letter - {'A' * 60} - Title.pdf"


# --- build_letter_pdf ---

@pytest.mark.parametrize("text", ["", "   \n\n  ", None])
def test_empty_letter_raises(text):
    with pytest.raises(LetterPdfError, match="empty"):
        build_letter_pdf(text, company="Acme", candidate_name="Test Name", when=WHEN)


def test_output_is_a_pdf():
    result = build_letter_pdf(SAMPLE_LETTER, company="Acme", candidate_name="Test Name", when=WHEN)
    assert isinstance(result, bytes)
    assert result.startswith(b"%PDF-")


def test_blank_candidate_name_still_builds():
    result = build_letter_pdf(SAMPLE_LETTER, company="Acme", candidate_name="", when=WHEN)
    assert result.startswith(b"%PDF-")


def test_unmappable_character_in_body_raises_letter_pdf_error():
    with pytest.raises(LetterPdfError, match="U\\+20AC"):
        build_letter_pdf("A salary of €50k.", company="Acme", candidate_name="Test Name", when=WHEN)


def test_unmappable_character_in_company_raises_letter_pdf_error():
    with pytest.raises(LetterPdfError, match="U\\+015F"):
        build_letter_pdf("Dear team,", company="Baş Ltd", candidate_name="Test Name", when=WHEN)


def test_letter_longer_than_a_page_builds():
    long_letter = "\n\n".join(f"Paragraph {i}. " + "Delivery and stakeholder management. " * 12 for i in range(40))
    result = build_letter_pdf(long_letter, company="Acme", candidate_name="Test Name", when=WHEN)
    assert result.startswith(b"%PDF-")
