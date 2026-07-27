# Tests for self-corrections: "meet at five. I mean six" -> "meet at six".

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow_local import DEFAULT_SETTINGS, apply_self_corrections, clean_text


# ── The swap itself ─────────────────────────────────────────────────────

def test_benjamins_example():
    assert clean_text("Let's meet up at five. I mean six") == "Let's meet up at six"

def test_number_swap_with_comma():
    assert clean_text("meet at five, I mean six") == "Meet at six"

def test_digit_swap():
    assert clean_text("call me at 5. I mean 6") == "Call me at 6"

def test_clock_time_swap():
    assert clean_text("the train leaves at 5:30. I mean 6:30") == (
        "The train leaves at 6:30"
    )

def test_name_swap():
    assert clean_text("send it to Sarah. I mean Benjamin") == "Send it to Benjamin"

def test_weekday_swap():
    assert clean_text("see you Tuesday, I mean Wednesday") == "See you Wednesday"

def test_i_meant_also_works():
    assert clean_text("at five. I meant six") == "At six"

def test_swap_mid_sentence_keeps_rest():
    assert clean_text("meet at five. I mean six at the cafe") == (
        "Meet at six at the cafe"
    )

def test_spoken_period_then_correction():
    # "period" converts to "." first, then the correction resolves
    assert clean_text("meet at five period I mean six") == "Meet at six"


# ── Cases that must NOT fire ────────────────────────────────────────────

def test_discourse_marker_untouched():
    assert clean_text("I mean, that's crazy") == "I mean, that's crazy"

def test_unscopable_rephrase_untouched():
    # No safe way to know how much to delete - leave it alone
    assert clean_text("that's wild. I mean it's insane") == (
        "That's wild. I mean it's insane"
    )

def test_mismatched_kinds_untouched():
    # number vs. name - not a clean swap, leave it alone
    assert clean_text("at five. I mean Sarah") == "At five. I mean Sarah"

def test_same_word_untouched():
    assert clean_text("it was Sarah. I mean Sarah was there") == (
        "It was Sarah. I mean Sarah was there"
    )

def test_plain_sentence_untouched():
    assert clean_text("I mean every word of it") == "I mean every word of it"


# ── Config flag ─────────────────────────────────────────────────────────

def test_off_switch():
    settings = dict(DEFAULT_SETTINGS)
    settings["self_corrections"] = False
    assert clean_text("at five. I mean six", settings) == "At five. I mean six"

def test_default_is_on():
    assert DEFAULT_SETTINGS["self_corrections"] is True

def test_function_is_pure_string_level():
    assert apply_self_corrections("at five. I mean six") == "at six"


# ── v2: more markers, carrier words, chains, new scratch phrases ────────

from flow_local import is_scratch_command


def test_repeated_carrier_word():
    # Benjiman's real dictation that slipped through v1
    assert clean_text("I'm going to have a meeting at five, I mean, at six.") == (
        "I'm going to have a meeting at six."
    )

def test_no_wait_marker():
    assert clean_text("meet on Tuesday, no wait, Wednesday") == "Meet on Wednesday"

def test_wait_no_marker():
    assert clean_text("at 5, wait no, 6") == "At 6"

def test_i_meant_to_say_marker():
    assert clean_text("send it to Sarah, I meant to say Benjamin") == (
        "Send it to Benjamin"
    )

def test_make_that_marker():
    assert clean_text("the meeting is at 5, make that 6") == "The meeting is at 6"

def test_sorry_marker():
    assert clean_text("it costs fifty, sorry, sixty dollars") == (
        "It costs sixty dollars"
    )

def test_chained_corrections():
    assert clean_text("at five, I mean six, no wait seven") == "At seven"

def test_carrier_with_mismatched_kinds_untouched():
    assert clean_text("at five, I mean, at some point") == (
        "At five, I mean, at some point"
    )

def test_sorry_as_apology_untouched():
    assert clean_text("I broke it, sorry about that") == "I broke it, sorry about that"


def test_new_scratch_phrases():
    assert is_scratch_command("I didn't mean to say that.")
    assert is_scratch_command("I didn't mean that")
    assert is_scratch_command("undo that")
    assert is_scratch_command("erase that")

def test_scratch_requires_exact_phrase():
    assert not is_scratch_command("I didn't mean to say that about Sarah")
