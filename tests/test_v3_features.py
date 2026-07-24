# Tests for the v3 features: spoken punctuation, corrections,
# "scratch that" detection, and config loading.

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flow_local
from flow_local import (
    DEFAULT_SETTINGS,
    apply_corrections,
    clean_text,
    is_scratch_command,
    load_settings,
)


# ── Spoken punctuation ──────────────────────────────────────────────────

def test_comma():
    assert clean_text("hello comma world") == "Hello, world"

def test_period_and_sentence_capital():
    assert clean_text("that works period let's do it") == "That works. Let's do it"

def test_full_stop():
    assert clean_text("done full stop") == "Done."

def test_question_mark():
    assert clean_text("are you coming question mark") == "Are you coming?"

def test_exclamation_mark_and_point():
    assert clean_text("wow exclamation mark") == "Wow!"
    assert clean_text("wow exclamation point") == "Wow!"

def test_new_line():
    assert clean_text("first item new line second item") == "First item\nSecond item"

def test_new_paragraph():
    assert clean_text("hi Ben new paragraph thanks for the help") == (
        "Hi Ben\n\nThanks for the help"
    )

def test_spoken_punctuation_is_case_insensitive():
    assert clean_text("Hello Comma world") == "Hello, world"

def test_whispers_own_punctuation_does_not_double_up():
    # Whisper often writes "Hello, comma world" — the spoken comma should
    # not produce a second one.
    assert clean_text("Hello, comma world") == "Hello, world"

def test_stray_period_after_spoken_new_paragraph():
    assert clean_text("first new paragraph. second") == "First\nSecond".replace("\n", "\n\n")

# ── Smart punctuation mode (infers word vs. command) ────────────────────

def test_noun_use_with_determiner_kept():
    assert clean_text("it ends in a period") == "It ends in a period"

def test_noun_phrase_with_determiner_two_back_kept():
    assert clean_text("the trial period ends tomorrow") == (
        "The trial period ends tomorrow"
    )

def test_oxford_comma_kept():
    assert clean_text("use an Oxford comma here") == "Use an Oxford comma here"

def test_command_at_end_after_noun_still_converts():
    assert clean_text("close the door period") == "Close the door."

def test_command_mid_sentence_still_converts():
    assert clean_text("that works period let's ship it") == (
        "That works. Let's ship it"
    )

def test_always_mode_converts_everything():
    settings = dict(DEFAULT_SETTINGS)
    settings["spoken_punctuation_mode"] = "always"
    assert clean_text("the trial period ends", settings) == "The trial. Ends"

def test_off_mode_converts_nothing():
    settings = dict(DEFAULT_SETTINGS)
    settings["spoken_punctuation_mode"] = "off"
    assert clean_text("hello comma world", settings) == "Hello comma world"


def test_custom_punctuation_map():
    settings = dict(DEFAULT_SETTINGS)
    settings["spoken_punctuation"] = {"smiley": "🙂"}
    assert clean_text("great job smiley", settings) == "Great job 🙂"


# ── Corrections ─────────────────────────────────────────────────────────

def test_correction_basic():
    assert apply_corrections("I use cloud code daily", {"cloud code": "Claude Code"}) == (
        "I use Claude Code daily"
    )

def test_correction_case_insensitive():
    assert apply_corrections("Cloud Code is great", {"cloud code": "Claude Code"}) == (
        "Claude Code is great"
    )

def test_correction_whole_words_only():
    # "cat" -> "Kat" must not touch "catalog"
    assert apply_corrections("the catalog has a cat", {"cat": "Kat"}) == (
        "the catalog has a Kat"
    )

def test_no_corrections_is_a_no_op():
    assert apply_corrections("unchanged text", {}) == "unchanged text"


# ── "Scratch that" detection ────────────────────────────────────────────

def test_scratch_that_plain():
    assert is_scratch_command("scratch that")

def test_scratch_that_cleaned_form():
    # As it arrives after clean_text: capitalized, maybe with punctuation
    assert is_scratch_command("Scratch that.")
    assert is_scratch_command("Delete that!")

def test_scratch_not_triggered_inside_sentence():
    assert not is_scratch_command("please scratch that off the list")
    assert not is_scratch_command("delete that file")

def test_normal_text_is_not_scratch():
    assert not is_scratch_command("Hello world")


# ── Config loading ──────────────────────────────────────────────────────

def test_first_run_writes_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(flow_local, "CONFIG_PATH", str(cfg))
    settings, error = load_settings()
    assert error is None
    assert settings == DEFAULT_SETTINGS
    assert cfg.exists()
    on_disk = json.loads(cfg.read_text())
    assert on_disk["hotkey"] == "alt_r"
    assert on_disk["model_size"] == "small.en"

def test_user_overrides_merge_with_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"hotkey": "cmd_r", "vocabulary": ["Benjiman"]}))
    monkeypatch.setattr(flow_local, "CONFIG_PATH", str(cfg))
    settings, error = load_settings()
    assert error is None
    assert settings["hotkey"] == "cmd_r"            # user's choice
    assert settings["vocabulary"] == ["Benjiman"]   # user's choice
    assert settings["model_size"] == "small.en"     # default filled in
    assert settings["fillers"] == DEFAULT_SETTINGS["fillers"]

def test_newly_introduced_settings_written_back(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"hotkey": "cmd_r"}))
    monkeypatch.setattr(flow_local, "CONFIG_PATH", str(cfg))
    load_settings()
    on_disk = json.loads(cfg.read_text())
    assert on_disk["hotkey"] == "cmd_r"                    # user value kept
    assert on_disk["auto_learn_vocabulary"] is True        # new key now visible
    assert on_disk["spoken_punctuation_mode"] == "smart"

def test_broken_config_returns_defaults_and_error(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text("{ this is not json")
    monkeypatch.setattr(flow_local, "CONFIG_PATH", str(cfg))
    settings, error = load_settings()
    assert settings == DEFAULT_SETTINGS
    assert error is not None
    # The broken file must NOT be overwritten
    assert cfg.read_text() == "{ this is not json"
