# Tests for learning corrections from backspace-and-retype edits
# (the "dot dot dot" -> "..." trick).

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow_local import (
    EDIT_PAUSE_SECONDS,
    EDIT_WATCH_SECONDS,
    EditWatcher,
    apply_corrections,
    tidy_spacing,
)

BACKSPACE = 51
RETURN = 36
LEFT_ARROW = 123

DICT = {"there", "their", "see", "you", "dot", "the", "and"}


def make_watcher(learned):
    return EditWatcher(DICT, lambda wrong, fixed: learned.append((wrong, fixed)))


def type_text(w, text, t):
    for ch in text:
        w.observe_key(0, ch, False, t)


def backspace(w, count, t):
    for _ in range(count):
        w.observe_key(BACKSPACE, "\x7f", False, t)


# ── The headline case ───────────────────────────────────────────────────

def test_dot_dot_dot_becomes_ellipsis():
    learned = []
    w = make_watcher(learned)
    w.start("See you there dot dot dot ", 0.0)   # what got pasted
    backspace(w, len("dot dot dot "), 1.0)        # user erases the tail
    type_text(w, "...", 2.0)                      # and types the fix
    w.tick(2.0 + EDIT_PAUSE_SECONDS + 0.1)        # short pause -> learned
    assert learned == [("dot dot dot", "...")]

def test_learned_rule_then_applies():
    fixed = apply_corrections("See you there dot dot dot", {"dot dot dot": "..."})
    assert tidy_spacing(fixed) == "See you there..."

def test_enter_finalizes_immediately():
    learned = []
    w = make_watcher(learned)
    w.start("okay dot dot dot ", 0.0)
    backspace(w, len("dot dot dot "), 1.0)
    type_text(w, "...", 1.5)
    w.observe_key(RETURN, "\r", False, 2.0)
    assert learned == [("dot dot dot", "...")]

def test_new_dictation_flushes_pending_fix():
    learned = []
    w = make_watcher(learned)
    w.start("ping the benjiman ", 0.0)
    backspace(w, len("benjiman "), 1.0)
    type_text(w, "Benjiman", 1.5)
    w.flush(1.6)  # hotkey pressed again right away
    assert learned == [("benjiman", "Benjiman")]


# ── Guards: things that must NOT become rules ───────────────────────────

def test_single_common_word_swap_not_learned():
    # Fixing a homophone once ("there" -> "their") must not rewrite every
    # future "there".
    learned = []
    w = make_watcher(learned)
    w.start("over there ", 0.0)
    backspace(w, len("there "), 1.0)
    type_text(w, "their", 2.0)
    w.tick(5.0)
    assert learned == []

def test_single_unusual_word_swap_is_learned():
    learned = []
    w = make_watcher(learned)
    w.start("ask benjamin ", 0.0)
    backspace(w, len("benjamin "), 1.0)
    type_text(w, "Benjiman", 2.0)
    w.tick(5.0)
    assert learned == [("benjamin", "Benjiman")]

def test_typing_without_deleting_is_not_a_fix():
    learned = []
    w = make_watcher(learned)
    w.start("hello world ", 0.0)
    type_text(w, "more words", 1.0)
    w.tick(5.0)
    assert learned == []

def test_click_aborts():
    learned = []
    w = make_watcher(learned)
    w.start("hello world ", 0.0)
    backspace(w, 6, 1.0)
    w.abort()  # mouse click
    type_text(w, "there", 2.0)
    w.tick(5.0)
    assert learned == []

def test_arrow_key_aborts():
    learned = []
    w = make_watcher(learned)
    w.start("hello world ", 0.0)
    backspace(w, 6, 1.0)
    w.observe_key(LEFT_ARROW, "", False, 1.5)
    type_text(w, "there", 2.0)
    w.tick(5.0)
    assert learned == []

def test_command_shortcut_aborts():
    learned = []
    w = make_watcher(learned)
    w.start("hello world ", 0.0)
    backspace(w, 6, 1.0)
    w.observe_key(0, "s", True, 1.5)  # e.g. Cmd-S
    type_text(w, "there", 2.0)
    w.tick(5.0)
    assert learned == []

def test_backspacing_past_the_paste_aborts():
    learned = []
    w = make_watcher(learned)
    w.start("hi ", 0.0)
    backspace(w, 10, 1.0)  # ate into text that isn't ours
    type_text(w, "hello", 2.0)
    w.tick(5.0)
    assert learned == []

def test_mid_word_deletion_not_learned():
    # Erasing "orld " leaves a fragment that would never match again
    learned = []
    w = make_watcher(learned)
    w.start("hello world ", 0.0)
    backspace(w, len("orld "), 1.0)
    type_text(w, "ipes", 2.0)
    w.tick(5.0)
    assert learned == []

def test_long_rephrase_not_learned():
    learned = []
    w = make_watcher(learned)
    w.start("let us grab some food at the usual place tonight ", 0.0)
    backspace(w, len("some food at the usual place tonight "), 1.0)
    type_text(w, "dinner", 2.0)
    w.tick(5.0)
    assert learned == []  # 6 words replaced -> rephrasing, not a fix

def test_watch_expires():
    learned = []
    w = make_watcher(learned)
    w.start("okay dot dot dot ", 0.0)
    backspace(w, len("dot dot dot "), EDIT_WATCH_SECONDS + 5)
    type_text(w, "...", EDIT_WATCH_SECONDS + 6)
    w.tick(EDIT_WATCH_SECONDS + 10)
    assert learned == []


# ── Small ergonomics ────────────────────────────────────────────────────

def test_typo_in_replacement_backspaces_the_buffer():
    learned = []
    w = make_watcher(learned)
    w.start("okay dot dot dot ", 0.0)
    backspace(w, len("dot dot dot "), 1.0)
    type_text(w, "..,", 2.0)
    w.observe_key(BACKSPACE, "\x7f", False, 2.1)  # fix the typo
    type_text(w, ".", 2.2)
    w.tick(6.0)
    assert learned == [("dot dot dot", "...")]

def test_deletion_only_learns_nothing():
    learned = []
    w = make_watcher(learned)
    w.start("hello world ", 0.0)
    backspace(w, len("world "), 1.0)
    w.tick(10.0)
    assert learned == []
