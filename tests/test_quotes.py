# Tests for spoken quotes: "quote ... end quote" -> "..."

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow_local import apply_spoken_quotes, clean_text


# ── Conversion ──────────────────────────────────────────────────────────

def test_bare_quote_pair():
    assert clean_text("she said quote I'll be there end quote") == (
        'She said "I\'ll be there"'
    )

def test_unquote_closes():
    assert clean_text("he told me quote don't worry about it unquote.") == (
        'He told me "don\'t worry about it."'
    )

def test_open_close_quote():
    assert clean_text("open quote hello close quote") == '"Hello"'

def test_begin_quote():
    assert clean_text("begin quote testing end quote") == '"Testing"'

def test_punctuation_tucks_inside():
    assert clean_text("she said quote yes end quote period") == 'She said "yes."'

def test_spoken_punctuation_inside_quote():
    assert clean_text("say quote hello comma world end quote") == (
        'Say "hello, world"'
    )

def test_quoted_speech_capitalized_after_comma():
    assert clean_text("she said, quote we should go end quote") == (
        'She said, "We should go"'
    )


# ── Cases that must stay literal ────────────────────────────────────────

def test_noun_quote_untouched():
    assert clean_text("I got a quote for the job") == "I got a quote for the job"

def test_verb_quote_untouched():
    assert clean_text("to quote Benjamin, it works") == "To quote Benjamin, it works"

def test_bare_quote_without_close_untouched():
    assert clean_text("that's a great quote") == "That's a great quote"

def test_orphan_close_untouched():
    assert clean_text("read the quote end quote") == "Read the quote end quote"

def test_off_mode_leaves_quotes_alone():
    from flow_local import DEFAULT_SETTINGS
    settings = dict(DEFAULT_SETTINGS)
    settings["spoken_punctuation_mode"] = "off"
    assert clean_text("say quote hi end quote", settings) == "Say quote hi end quote"

def test_function_is_pure_string_level():
    assert apply_spoken_quotes("quote hi end quote") == ' "hi" '
