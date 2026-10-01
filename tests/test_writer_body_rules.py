"""
The letter argues, it does not recite.

Rule 6 sets the envelope; rule 7 sets what the body must do - open on the work,
select, name what the CV names, attach the stated result, use its own words, one
gap for a hard requirement only, a short close. The shape is an annotated
outline, never prose: persona.md's finished example was reproduced near-verbatim
in letters for ten companies, so the skeleton is written so that no line can be
lifted into a letter as written.

The scoring notes are renamed and delimited. They justify a score, they quote the
posting, and they are not a source of facts.

These tests read SYSTEM_PROMPT and USER_PROMPT_TEMPLATE only. They never read
data/knowledge/, which is personal and gitignored.
"""
import re

from skills.writer import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

SKELETON_HEADER = "THE SHAPE OF THE BODY (an outline, not text to copy):"
FIRST_PERSON = re.compile(r"\b(i|i'm|i've|my|me|mine)\b", re.IGNORECASE)


def skeleton_lines():
    """The bracketed outline lines, in order."""
    after = SYSTEM_PROMPT.index(SKELETON_HEADER) + len(SKELETON_HEADER)
    lines = []
    for line in SYSTEM_PROMPT[after:].splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("["):
            break
        lines.append(line)
    return lines


# --- Rule 7 ---

def test_rule_seven_sits_between_the_envelope_and_the_output_format():
    six = SYSTEM_PROMPT.index("6. LETTER STRUCTURE:")
    seven = SYSTEM_PROMPT.index("7. WHAT THE BODY MUST DO:")
    eight = SYSTEM_PROMPT.index("8. OUTPUT FORMAT:")
    assert six < seven < eight


def test_rule_seven_names_every_requirement():
    for phrase in [
        "Open with a thesis",
        "Exactly two arguments",
        "Name what the CV names",
        "Attach the result the CV states",
        "Use your own words, not the posting's",
        "One gap, only when the requirement is hard",
        "Close short",
        "200 to 300 words",
    ]:
        assert phrase in SYSTEM_PROMPT, phrase


def test_rule_seven_wins_over_a_conflicting_persona_instruction():
    """Rule 4 binds the persona's length and structure; rule 7 sets both, so precedence is stated."""
    assert "Where a persona instruction conflicts with rule 7, rule 7 wins." in SYSTEM_PROMPT


def test_named_counterparty_is_attributed_to_the_employer():
    assert "never as the candidate's own client, customer or account" in SYSTEM_PROMPT
    assert "If the CV names no counterparty for a piece of work, do not supply one." in SYSTEM_PROMPT


def test_the_gap_sentence_is_limited_to_hard_requirements():
    assert "required, essential, mandatory or a minimum" in SYSTEM_PROMPT
    assert "treat an unmarked requirement as not hard" in SYSTEM_PROMPT
    assert "If nothing hard is unevidenced, write no gap sentence at all." in SYSTEM_PROMPT


def test_reuse_rule_leads_with_the_behaviour_and_keeps_the_floor():
    """The model is told not to copy; the number exists so a checker and the prompt agree."""
    assert "Do not copy wording from the job details into the letter" in SYSTEM_PROMPT
    assert "six or more consecutive words" in SYSTEM_PROMPT


# --- The skeleton teaches shape, not wording ---

def test_skeleton_is_five_bracketed_instruction_lines():
    lines = skeleton_lines()
    assert len(lines) == 5
    for line in lines:
        assert line.startswith("[") and line.endswith("]"), line


def test_skeleton_contains_no_first_person_pronoun():
    """A letter is first person. No skeleton line can be pasted into one without being rewritten."""
    for line in skeleton_lines():
        found = FIRST_PERSON.findall(line)
        assert not found, (line, found)


def test_output_format_is_rule_eight_and_no_rule_number_repeats():
    assert "8. OUTPUT FORMAT:" in SYSTEM_PROMPT
    assert "7. OUTPUT FORMAT:" not in SYSTEM_PROMPT
    numbers = re.findall(r"^(\d)\. ", SYSTEM_PROMPT, re.MULTILINE)
    assert numbers == ["1", "2", "3", "4", "5", "6", "7", "8"]


def test_output_format_forbids_emitting_the_skeleton():
    assert "Never output the bracketed labels from rule 7's shape" in SYSTEM_PROMPT


def test_rule_seven_opens_with_a_thesis():
    assert "Open with a thesis" in SYSTEM_PROMPT
    assert "its own paragraph of one or two sentences" in SYSTEM_PROMPT
    assert "The thesis previews; it asserts nothing the two evidence paragraphs do not prove." in SYSTEM_PROMPT


def test_rule_seven_requires_exactly_two_arguments():
    """One argument then CV recital: (b) allowed "one or two" and the model took one."""
    assert "Exactly two arguments" in SYSTEM_PROMPT
    assert "exactly two evidence paragraphs, one per requirement" in SYSTEM_PROMPT
    assert "never add another CV item" in SYSTEM_PROMPT


def test_partial_use_rule_forbids_reproducing_a_cv_list():
    """A CV line bundling several methods was reproduced whole, defeating (h)."""
    assert "j) Partial use." in SYSTEM_PROMPT
    assert "Never reproduce a CV line's list" in SYSTEM_PROMPT


def test_reuse_rule_permits_the_role_title():
    """(a) lets the title appear in the thesis, so (e) names the exception at the point of use."""
    assert "other than the role's title, which (a) permits" in SYSTEM_PROMPT


def test_rule_seven_has_methods_and_tenure_items():
    seven = SYSTEM_PROMPT.index("7. WHAT THE BODY MUST DO:")
    g = SYSTEM_PROMPT.index("g) Close short.")
    h = SYSTEM_PROMPT.index("h) Methods.")
    i = SYSTEM_PROMPT.index("i) Tenure.")
    j = SYSTEM_PROMPT.index("j) Partial use.")
    eight = SYSTEM_PROMPT.index("8. OUTPUT FORMAT:")
    assert seven < g < h < i < j < eight


def test_methods_rule_requires_the_posting_to_ask():
    assert "only if the posting asks for it and the CV evidences it" in SYSTEM_PROMPT
    assert "even when the CV lists it first" in SYSTEM_PROMPT


def test_tenure_rule_forbids_joining_roles():
    """A letter joined two roles, placing the current role inside an earlier span."""
    assert "Attribute every duration to the role that produced it" in SYSTEM_PROMPT
    assert '"including", "during which"' in SYSTEM_PROMPT
    assert "Describe the current role as current." in SYSTEM_PROMPT


def test_system_prompt_is_still_a_constant_with_no_format_placeholders():
    assert "{" not in SYSTEM_PROMPT
    assert "}" not in SYSTEM_PROMPT


# --- The scoring notes ---

def test_the_old_outline_heading_is_gone():
    assert "WHY THIS JOB MATCHES" not in USER_PROMPT_TEMPLATE


def test_the_writer_never_receives_the_scoring_notes():
    """D11: the notes leaked as an outline, as a fact source and as a gap source. The input is gone."""
    for text in (SYSTEM_PROMPT, USER_PROMPT_TEMPLATE):
        assert "scoring_notes_untrusted" not in text
        assert "SCORING NOTES" not in text
        assert "scoring notes" not in text
    assert "{match_reasons}" not in USER_PROMPT_TEMPLATE
