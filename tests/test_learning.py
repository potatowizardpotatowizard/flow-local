# Tests for the auto-learning personal dictionary.

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flow_local
from flow_local import (
    build_initial_prompt,
    extract_learnable_words,
    learn_words,
    load_learned_words,
    save_learned_words,
    unlearn_words,
)

# A miniature "system dictionary" so tests don't depend on the machine.
DICT = {"the", "meeting", "moved", "to", "monday", "with", "team", "document",
        "is", "ready", "for", "review", "quick", "brown", "fox", "and", "i",
        "talked", "about", "it", "use", "used", "using", "documents",
        "ask", "ping", "deploy", "acting", "one", "two", "once", "twice",
        "appeared", "up"}

SETTINGS = {"vocabulary": [], "auto_learn_vocabulary": True}


# ── What gets learned ───────────────────────────────────────────────────

def test_unknown_word_is_learned():
    assert extract_learnable_words("the kubectl document is ready", DICT) == ["kubectl"]

def test_dictionary_words_are_not_learned():
    assert extract_learnable_words("The meeting moved to Monday", DICT) == []

def test_simple_plurals_count_as_known():
    # "meetings" isn't in the tiny dict but "meeting" is
    assert extract_learnable_words("the meetings moved", DICT) == []

def test_unusual_name_is_learned():
    words = extract_learnable_words("I talked with Benjiman about it", DICT)
    assert words == ["Benjiman"]

def test_dictionary_proper_nouns_are_not_learned():
    # "Monday" is capitalized mid-sentence, but the dictionary knows it,
    # so the model spells it fine. No need to spend a hint slot on it.
    assert extract_learnable_words("the meeting moved to Monday", DICT) == []

def test_without_system_dict_falls_back_to_proper_nouns():
    words = extract_learnable_words("I talked with Benjiman on Monday. Great chat.", set())
    assert words == ["Benjiman", "Monday"]

def test_sentence_start_capital_alone_is_not_learned():
    # Capitalized only because it starts the sentence (no-dictionary mode)
    assert extract_learnable_words("Meeting moved. Review is ready.", set()) == []

def test_short_words_and_contractions_skipped():
    assert extract_learnable_words("it's ok qu xyzzyx", DICT) == ["xyzzyx"]

def test_manual_vocabulary_not_relearned():
    assert extract_learnable_words("ask Benjiman", DICT, ["Benjiman"]) == []


# ── Counting, forgetting, pruning ───────────────────────────────────────

def test_learn_words_counts_repetitions():
    store = {}
    learn_words(store, "deploy with kubectl", DICT, SETTINGS)
    learn_words(store, "kubectl is acting up", DICT, SETTINGS)
    assert store["kubectl"]["count"] == 2
    assert store["kubectl"]["text"] == "kubectl"

def test_unlearn_removes_single_sighting():
    store = {}
    learn_words(store, "deploy with kubectl", DICT, SETTINGS)
    unlearn_words(store, "deploy with kubectl")
    assert "kubectl" not in store

def test_unlearn_only_decrements_established_words():
    store = {}
    learn_words(store, "kubectl one", DICT, SETTINGS)
    learn_words(store, "kubectl two", DICT, SETTINGS)
    unlearn_words(store, "kubectl two")
    assert store["kubectl"]["count"] == 1

def test_store_is_pruned_to_limit():
    store = {}
    for i in range(flow_local.LEARNED_STORE_LIMIT + 40):
        learn_words(store, f"zzworda{i} appeared", DICT, SETTINGS)
    assert len(store) <= flow_local.LEARNED_STORE_LIMIT


# ── The hint handed to Whisper ──────────────────────────────────────────

def test_prompt_needs_two_sightings():
    store = {}
    learn_words(store, "kubectl once", DICT, SETTINGS)
    assert build_initial_prompt(SETTINGS, store) is None
    learn_words(store, "kubectl twice", DICT, SETTINGS)
    assert build_initial_prompt(SETTINGS, store) == "Vocabulary: kubectl"

def test_prompt_puts_manual_vocabulary_first_and_dedupes():
    store = {"kubectl": {"text": "kubectl", "count": 5, "last": "2026-07-24"}}
    settings = {"vocabulary": ["Claude Code", "kubectl"]}
    assert build_initial_prompt(settings, store) == "Vocabulary: Claude Code, kubectl"

def test_prompt_caps_word_count():
    store = {
        f"word{i:03d}": {"text": f"word{i:03d}", "count": 3, "last": "2026-07-24"}
        for i in range(flow_local.PROMPT_WORD_LIMIT + 30)
    }
    prompt = build_initial_prompt(SETTINGS, store)
    assert len(prompt.split(", ")) == flow_local.PROMPT_WORD_LIMIT

def test_empty_prompt_is_none():
    assert build_initial_prompt(SETTINGS, {}) is None


# ── Persistence ─────────────────────────────────────────────────────────

def test_learned_words_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "learned_words.json"
    monkeypatch.setattr(flow_local, "LEARNED_PATH", str(path))
    store = {}
    learn_words(store, "ping Benjiman about kubectl", DICT, SETTINGS)
    save_learned_words(store)
    assert load_learned_words() == store
    assert set(json.loads(path.read_text())) == {"benjiman", "kubectl"}

def test_missing_or_broken_file_gives_empty_store(tmp_path, monkeypatch):
    path = tmp_path / "learned_words.json"
    monkeypatch.setattr(flow_local, "LEARNED_PATH", str(path))
    assert load_learned_words() == {}
    path.write_text("not json at all")
    assert load_learned_words() == {}
