"""
The Persona section supplies voice, not facts.

persona.md carried specific project claims -- a certification outcome, a
side-by-side comparison, a budget renegotiation -- and the writer asserted them as
the candidate's experience. Rule 2 already bound claims to the CV section; it now
says outright that the Persona section is not a source of facts.

These tests read only SYSTEM_PROMPT. They never read data/knowledge/persona.md,
which is gitignored and personal.
"""
from skills.writer import SYSTEM_PROMPT

PERSONA_SENTENCE = "The Persona section is not a source of facts"


def test_system_prompt_says_the_persona_is_not_a_source_of_facts():
    assert PERSONA_SENTENCE in SYSTEM_PROMPT
    assert "may be asserted, paraphrased or adapted as the candidate's experience" in SYSTEM_PROMPT


def test_persona_sentence_sits_inside_the_anti_fabrication_rule():
    start = SYSTEM_PROMPT.index("2. ANTI-FABRICATION RULE")
    end = SYSTEM_PROMPT.index("3. UNTRUSTED DATA RULE")
    assert start < SYSTEM_PROMPT.index(PERSONA_SENTENCE) < end


def test_cv_only_wording_is_unchanged():
    assert "explicitly present in the CV section below" in SYSTEM_PROMPT
    assert "never invent or imply qualifications that are not there" in SYSTEM_PROMPT


def test_rule_four_still_limits_the_persona_to_form():
    """Rule 4 governs tone, voice, length and structure; the new sentence governs facts."""
    assert "Follow the persona constraints (tone, voice, length, structure)" in SYSTEM_PROMPT
