# Tests that pin down the clean_text() behavior Flow Local has always had.
# If these break, dictation output changed — investigate before shipping.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow_local import clean_text


# ── Filler removal ──────────────────────────────────────────────────────

def test_strips_um_and_uh():
    assert clean_text("so um I think uh we should go") == "So I think we should go"

def test_strips_stretched_fillers():
    assert clean_text("umm let me think hmmm about it") == "Let me think about it"

def test_strips_filler_with_surrounding_commas():
    assert clean_text("we should, um, move the meeting") == "We should move the meeting"

def test_strips_mm_hmm():
    assert clean_text("mm-hmm that works for me") == "That works for me"

def test_does_not_strip_words_containing_fillers():
    # "umbrella" contains "um" but must survive
    assert clean_text("bring the umbrella") == "Bring the umbrella"


# ── Duplicate-word collapse ─────────────────────────────────────────────

def test_collapses_repeated_word():
    assert clean_text("send the the report") == "Send the report"

def test_collapses_triple_repetition():
    assert clean_text("I I I want to go") == "I want to go"

def test_collapses_repeats_case_insensitively():
    assert clean_text("The the meeting moved") == "The meeting moved"


# ── Spacing and punctuation tidy-up ─────────────────────────────────────

def test_collapses_extra_whitespace():
    assert clean_text("hello    world") == "Hello world"

def test_no_space_before_punctuation():
    assert clean_text("hello , world .") == "Hello, world."

def test_collapses_duplicate_commas():
    assert clean_text("first, , second") == "First, second"

def test_strips_leading_orphan_punctuation():
    assert clean_text(", so anyway") == "So anyway"


# ── Capitalization ──────────────────────────────────────────────────────

def test_capitalizes_first_letter():
    assert clean_text("hello there") == "Hello there"

def test_capitalizes_each_sentence():
    assert clean_text("first thing. second thing. third") == (
        "First thing. Second thing. Third"
    )

def test_capitalizes_after_question_and_exclamation():
    assert clean_text("really? yes! great") == "Really? Yes! Great"


# ── Edge cases ──────────────────────────────────────────────────────────

def test_empty_string():
    assert clean_text("") == ""

def test_only_fillers_becomes_empty():
    assert clean_text("um uh umm") == ""

def test_plain_sentence_untouched():
    assert clean_text("The quick brown fox jumps over the lazy dog.") == (
        "The quick brown fox jumps over the lazy dog."
    )
