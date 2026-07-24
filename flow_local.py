#!/usr/bin/env python3
"""
Flow Local — offline dictation for macOS.  This file is the engine.

Hold the hotkey (Right Option by default), speak, release: your words are
transcribed locally with Whisper, cleaned up, and typed into whatever app
is in front.  Double-tap the hotkey to lock recording on (hands-free);
tap once to stop.

No cloud. No API keys. No telemetry.

You can run this file directly for a terminal-only experience
(`bash run.sh`), but the nicer way is the menu bar app: `flow_menubar.py`
(built into "Flow Local.app" by `make_app.sh`).

All user settings live in config.json next to this file — created with
sensible defaults on first run.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOCK_PATH = os.path.join(APP_DIR, ".flow-local.lock")
LEARNED_PATH = os.path.join(APP_DIR, "learned_words.json")
SYSTEM_DICT_PATH = "/usr/share/dict/words"

SAMPLE_RATE = 16000

# ──────────────────────────── Settings ─────────────────────────────────

# Every user-tweakable setting and its default. config.json only needs to
# contain the keys you want to change; missing keys fall back to these.
DEFAULT_SETTINGS = {
    # Key to hold while talking: "alt_r" (Right Option), "alt_l", "cmd_r",
    # "ctrl_r", or "f13" ... "f20".
    "hotkey": "alt_r",
    # Whisper model: tiny.en | base.en | small.en | medium.en
    # (use "small" without ".en" for non-English dictation)
    "model_size": "small.en",
    # Play a subtle sound when recording starts/stops.
    "play_sounds": True,
    # false: the microphone is opened only while you're recording, so the
    #        orange mic indicator appears only when you dictate.
    # true:  keep the mic stream open all the time for slightly faster
    #        recording start (audio is still discarded unless recording).
    "keep_mic_open": False,
    # Add a trailing space after each dictation.
    "append_space": True,
    # Ignore recordings shorter than this many seconds (accidental taps).
    "min_seconds": 0.4,
    # Double-tap the hotkey within this many seconds to lock recording on.
    "double_tap_seconds": 0.5,
    # A press shorter than this counts as a "tap" (not a hold-to-talk).
    "tap_seconds": 0.3,
    # Your typing speed, used for the "minutes saved" stat.
    "typing_wpm": 40,
    # Filler words to strip. Each one also matches with its last letter
    # stretched out ("um" also matches "umm", "ummm", ...).
    "fillers": ["um", "uh", "uhm", "erm", "hmm", "mm-hmm", "mmm"],
    # How to treat spoken punctuation words:
    #   "smart"  - convert, but leave the word alone when it's clearly used
    #              as a noun ("the trial period ends", "an Oxford comma")
    #   "always" - convert every occurrence
    #   "off"    - never convert
    "spoken_punctuation_mode": "smart",
    # Say the word on the left, get the symbol on the right.
    "spoken_punctuation": {
        "comma": ",",
        "period": ".",
        "full stop": ".",
        "question mark": "?",
        "exclamation mark": "!",
        "exclamation point": "!",
        "colon": ":",
        "semicolon": ";",
        "dash": "-",
        "new line": "\n",
        "new paragraph": "\n\n",
    },
    # Names and jargon Whisper should recognize (passed as a hint to the
    # model). Example: ["Benjiman", "Claude Code", "kubectl"]
    "vocabulary": [],
    # Automatically learn unusual words (names, jargon) from what you
    # dictate, so Whisper recognizes them next time. Stored locally in
    # learned_words.json; words you "scratch" are un-learned.
    "auto_learn_vocabulary": True,
    # Forced fixes applied after transcription (case-insensitive match).
    # Example: {"cloud code": "Claude Code"}
    "corrections": {},
    # When no text field is focused (e.g. you're just on the desktop),
    # keep the dictation on the clipboard and show a notification instead
    # of typing into nowhere. Set false to always attempt the paste.
    "copy_when_no_text_field": True,
    # Learn corrections from your edits: if you backspace part of a fresh
    # dictation and retype it, the fix becomes a `corrections` rule
    # (e.g. say "dot dot dot", replace the literal words with "...", and
    # it converts automatically from then on). Menu bar app only.
    "learn_from_edits": True,
}


def load_settings():
    """Read config.json, creating it with defaults on first run.

    Returns (settings_dict, error_message_or_None). A broken config.json is
    never overwritten — you get defaults plus an error to surface in the UI.
    """
    if not os.path.exists(CONFIG_PATH):
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS), None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("config.json must contain a JSON object")
    except Exception as e:
        return dict(DEFAULT_SETTINGS), f"config.json could not be read: {e}"
    merged = dict(DEFAULT_SETTINGS)
    merged.update(user)
    # Write back any settings introduced by an update, so they're visible
    # (with their defaults) when you open the file.
    if any(key not in user for key in DEFAULT_SETTINGS):
        save_settings(merged)
    return merged, None


def save_settings(settings):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ──────────────────────────── Text cleanup ─────────────────────────────

# Newlines are represented by this placeholder while cleanup runs, so the
# whitespace-collapsing rules can't eat them. Restored at the end.
_NL = "\x00"


def _filler_regex(fillers):
    """Build one regex matching any filler word (with a stretchy last letter)."""
    parts = [re.escape(f) + "+" for f in fillers]
    return r"\b(?:" + "|".join(parts) + r")\b"


# Words that mark a spoken-punctuation word as a real noun when they appear
# right before it ("a period", "in that period", "an Oxford comma").
_DETERMINERS = {
    "the", "a", "an", "this", "that", "these", "those",
    "my", "your", "his", "her", "its", "our", "their",
    "each", "every", "per", "one", "no", "any", "some", "another",
}
# Two words back, only articles/possessives are trustworthy ("the trial
# period ends") — "this"/"that" there is usually a pronoun ("that works
# period" is a command).
_ARTICLES = {
    "the", "a", "an",
    "my", "your", "his", "her", "its", "our", "their",
}


def _meant_literally(match):
    """True when a spoken-punctuation word is probably meant as a word.

    Rules, in order:
      1. A determiner immediately before -> literal ("in a period").
      2. Nothing after it (end of dictation) -> punctuation ("close it period").
      3. A determiner two words back -> literal ("the trial period ends").
      4. Otherwise -> punctuation.
    """
    before_words = re.findall(r"[A-Za-z']+", match.string[: match.start()])[-2:]
    if before_words and before_words[-1].lower() in _DETERMINERS:
        return True
    if not re.search(r"[A-Za-z]", match.string[match.end():]):
        return False
    return len(before_words) == 2 and before_words[0].lower() in _ARTICLES


def clean_text(text, settings=None):
    """Rule-based cleanup: spoken punctuation, fillers, spacing, capitals."""
    s = settings if settings is not None else DEFAULT_SETTINGS

    # Normalize all whitespace first so phrase matching is predictable.
    text = re.sub(r"\s+", " ", text).strip()

    # Spoken punctuation: "comma" -> "," etc. Longest phrases first so
    # "question mark" wins before any shorter overlap. Newline values
    # become placeholders until the very end. In "smart" mode (default),
    # words that look like real nouns are left alone — see _meant_literally.
    mode = s.get("spoken_punctuation_mode", "smart")
    punct = s.get("spoken_punctuation", {}) if mode != "off" else {}
    for phrase in sorted(punct, key=len, reverse=True):
        symbol = punct[phrase].replace("\n", _NL)

        def _replace(match, _symbol=symbol):
            if mode == "smart" and _meant_literally(match):
                return match.group(0)
            return _symbol

        text = re.sub(r"\b" + re.escape(phrase) + r"\b", _replace, text, flags=re.IGNORECASE)

    # Remove filler words along with the commas that set them off
    # ("we should, um, move" -> "we should move")
    fillers = s.get("fillers", [])
    if fillers:
        text = re.sub(
            r"(?:[,;:]\s*)?" + _filler_regex(fillers) + r"\s*[,;:]?",
            " ",
            text,
            flags=re.IGNORECASE,
        )

    # Collapse immediate word repetitions left by hesitations ("the the report")
    text = re.sub(r"\b(\w+)(\s+\1)+\b", r"\1", text, flags=re.IGNORECASE)

    # Collapse runs of whitespace
    text = re.sub(r"\s+", " ", text)

    # No space before punctuation
    text = re.sub(r"\s+([,.!?;:%)])", r"\1", text)

    # Collapse duplicate punctuation left behind by removed fillers (", ,")
    text = re.sub(r"([,;:])\s*[,;:]+", r"\1", text)
    text = re.sub(r"([.!?])\s*[,;:]+", r"\1", text)

    # Drop stray punctuation Whisper put right after a spoken "new line" /
    # "new paragraph" ("... new paragraph." -> just the newline).
    text = re.sub(_NL + r"[ ]*[,.;:]+", _NL, text)

    # Strip leading orphaned punctuation (and leading newlines)
    text = re.sub(r"^[\s,.;:!?\x00]+", "", text)

    # Restore newlines, without spaces hugging them
    text = re.sub(r" *(\x00+) *", r"\1", text).replace(_NL, "\n")

    text = text.strip()

    # Capitalize the first letter of the text, of each sentence, and of
    # each new line/paragraph
    def _cap(match):
        return match.group(1) + match.group(2).upper()

    if text:
        text = text[0].upper() + text[1:]
        text = re.sub(r"([.!?]\s+)([a-z])", _cap, text)
        text = re.sub(r"(\n+)([a-z])", _cap, text)

    return text


def apply_corrections(text, corrections):
    """Forced post-fixes from config.json, e.g. "cloud code" -> "Claude Code"."""
    for wrong in sorted(corrections, key=len, reverse=True):
        text = re.sub(
            r"\b" + re.escape(wrong) + r"\b",
            corrections[wrong].replace("\\", "\\\\"),
            text,
            flags=re.IGNORECASE,
        )
    return text


def is_scratch_command(text):
    """True if the dictation is only "scratch that" / "delete that"."""
    bare = re.sub(r"[^a-z ]", "", text.lower()).strip()
    return bare in ("scratch that", "delete that")


# ──────────────── Personal dictionary that learns ──────────────────────
#
# Whisper accepts a text hint (initial_prompt) that biases it toward
# words it should expect. Besides the manual `vocabulary` list, Flow
# Local mines every dictation for unusual words — names, jargon, anything
# not in the system dictionary — and keeps score in learned_words.json.
# Words seen at least twice get fed back to Whisper, so recognition of
# *your* words improves the more you dictate. Everything stays on disk,
# locally. "Scratch that" un-learns the words of the erased dictation.

LEARNED_STORE_LIMIT = 500  # most words kept on disk
PROMPT_WORD_LIMIT = 50     # most words hinted to Whisper per dictation
LEARNED_MIN_COUNT = 2      # times seen before a word is hinted


def load_system_dictionary():
    """Lowercased set of common English words (macOS ships one)."""
    try:
        with open(SYSTEM_DICT_PATH, encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip()}
    except OSError:
        return set()


def load_learned_words():
    try:
        with open(LEARNED_PATH, encoding="utf-8") as f:
            store = json.load(f)
        return store if isinstance(store, dict) else {}
    except Exception:
        return {}


def save_learned_words(store):
    with open(LEARNED_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _in_dictionary(word, system_dict):
    """Membership check that tolerates simple plural/verb endings, since
    the system word list only has base forms ("documents" -> "document")."""
    w = word.lower()
    forms = {w}
    if w.endswith("s"):
        forms.add(w[:-1])
    if w.endswith(("es", "ed", "ly")):
        forms.add(w[:-2])
    if w.endswith("d"):
        forms.add(w[:-1])
    if w.endswith("ing"):
        forms.update({w[:-3], w[:-3] + "e"})
    return any(f in system_dict for f in forms)


def extract_learnable_words(text, system_dict, already_known=()):
    """Words in a dictation worth remembering.

    Anything not in the system dictionary (jargon, unusual names) is
    learnable — words the dictionary knows, Whisper already spells fine.
    Without a system dictionary, fall back to mid-sentence capitalized
    words (proper nouns). Contractions are skipped.
    """
    known = {w.lower() for w in already_known}
    found = []
    for match in re.finditer(r"[A-Za-z][A-Za-z'-]*", text):
        word = match.group(0)
        if len(word) < 3 or "'" in word or word.lower() in known:
            continue
        if system_dict:
            learnable = not _in_dictionary(word, system_dict)
        else:
            before = text[: match.start()].rstrip(" \"'")
            at_sentence_start = not before or before[-1] in ".!?\n"
            learnable = (
                word[0].isupper() and word[1:].islower() and not at_sentence_start
            )
        if learnable:
            found.append(word)
    return found


def learn_words(store, text, system_dict, settings):
    """Update the learned-words store from one dictation."""
    vocabulary = settings.get("vocabulary", [])
    today = time.strftime("%Y-%m-%d")
    for word in extract_learnable_words(text, system_dict, vocabulary):
        entry = store.setdefault(word.lower(), {"text": word, "count": 0, "last": today})
        entry["count"] += 1
        entry["last"] = today
        entry["text"] = word  # remember the casing most recently used
    if len(store) > LEARNED_STORE_LIMIT:
        ranked = sorted(
            store.items(), key=lambda kv: (kv[1]["count"], kv[1]["last"]), reverse=True
        )
        store.clear()
        store.update(dict(ranked[:LEARNED_STORE_LIMIT]))


def unlearn_words(store, text):
    """Walk back the counts for an erased ("scratch that") dictation."""
    for match in re.finditer(r"[A-Za-z][A-Za-z'-]*", text):
        key = match.group(0).lower()
        entry = store.get(key)
        if entry:
            entry["count"] -= 1
            if entry["count"] <= 0:
                del store[key]


def build_initial_prompt(settings, store):
    """The vocabulary hint passed to Whisper: manual words first, then the
    best-established learned words, deduplicated, capped."""
    words = []
    seen = set()
    learned = sorted(
        store.values(), key=lambda e: (-e["count"], e["last"]), reverse=False
    )
    manual = list(settings.get("vocabulary", []))
    for word in manual + [e["text"] for e in learned if e["count"] >= LEARNED_MIN_COUNT]:
        if word.lower() not in seen:
            seen.add(word.lower())
            words.append(word)
        if len(words) >= PROMPT_WORD_LIMIT:
            break
    return ("Vocabulary: " + ", ".join(words)) if words else None


def tidy_spacing(text):
    """Re-fix "word ..." -> "word..." after corrections insert punctuation."""
    return re.sub(r"\s+([,.!?;:%)])", r"\1", text)


# ──────────────── Learning corrections from your edits ─────────────────
#
# Wispr-style: say "dot dot dot", watch it come out literally, backspace
# it and type "..." — Flow Local notices the fix and adds a rule to the
# `corrections` map in config.json, so it converts automatically from
# then on. Keystrokes are only watched for a short window right after a
# dictation, only the fixed phrase is kept, and every learned rule is
# visible (and deletable) in config.json.

EDIT_WATCH_SECONDS = 30.0    # stop watching this long after a paste
EDIT_PAUSE_SECONDS = 2.5     # a typing pause this long ends the fix
MAX_CORRECTION_WORDS = 4     # longer replacements are rephrasing, not fixes
MAX_REPLACEMENT_CHARS = 40

_KEY_BACKSPACE = 51
_KEYS_FINALIZE = {36, 76, 48}          # return, keypad enter, tab
_KEYS_ABORT = {53, 123, 124, 125, 126}  # escape, arrow keys


class EditWatcher:
    """Watches the brief window after a paste for backspace-and-retype.

    The frontend feeds it key events; when a fix is detected it calls
    on_learn(wrong_text, corrected_text). Anything that suggests the user
    moved on — clicking, arrow keys, app switching (command keys), typing
    without deleting first — cancels the watch.
    """

    def __init__(self, system_dict, on_learn):
        self.system_dict = system_dict
        self.on_learn = on_learn
        self._lock = threading.Lock()
        self._watch = None

    def start(self, pasted_text, now):
        with self._lock:
            self._watch = {
                "text": pasted_text,
                "backspaces": 0,
                "typed": "",
                "start": now,
                "last_key": now,
            }

    def abort(self):
        with self._lock:
            self._watch = None

    def observe_key(self, keycode, chars, has_command_modifier, now):
        learned = None
        with self._lock:
            w = self._watch
            if w is None:
                return
            if now - w["start"] > EDIT_WATCH_SECONDS or has_command_modifier:
                self._watch = None
                return
            if keycode == _KEY_BACKSPACE:
                if w["typed"]:
                    w["typed"] = w["typed"][:-1]  # typo in the replacement
                else:
                    w["backspaces"] += 1
                    if w["backspaces"] > len(w["text"]):
                        self._watch = None
                        return
                w["last_key"] = now
            elif keycode in _KEYS_FINALIZE:
                learned = self._take_correction()
            elif keycode in _KEYS_ABORT:
                self._watch = None
            else:
                printable = "".join(c for c in chars if c.isprintable())
                if not printable:
                    return
                if w["backspaces"] == 0:
                    self._watch = None  # typing onward, not fixing
                else:
                    w["typed"] += printable
                    w["last_key"] = now
        if learned:
            self.on_learn(*learned)

    def tick(self, now):
        """Call periodically: a pause after retyping completes the fix."""
        learned = None
        with self._lock:
            w = self._watch
            if w is None:
                return
            if now - w["start"] > EDIT_WATCH_SECONDS:
                self._watch = None
            elif w["typed"] and now - w["last_key"] > EDIT_PAUSE_SECONDS:
                learned = self._take_correction()
        if learned:
            self.on_learn(*learned)

    def flush(self, now):
        """A new dictation is starting — settle any pending fix now."""
        learned = None
        with self._lock:
            if self._watch is not None:
                learned = self._take_correction()
        if learned:
            self.on_learn(*learned)

    def _take_correction(self):
        """Turn the watched edit into a (wrong, fixed) pair — or None if it
        doesn't look like a safe, reusable correction. Clears the watch."""
        w, self._watch = self._watch, None
        if not w["backspaces"] or not w["typed"].strip():
            return None
        text = w["text"]
        removed = text[-w["backspaces"]:]
        # The deletion must start at a word boundary, or the "wrong" side
        # would be a fragment that never matches future dictations.
        if w["backspaces"] < len(text) and text[-w["backspaces"] - 1] not in " \n\t":
            return None
        wrong = removed.strip()
        fixed = w["typed"].strip()
        if not wrong or wrong == fixed or "\n" in wrong:
            return None
        words = wrong.split()
        if not 1 <= len(words) <= MAX_CORRECTION_WORDS:
            return None
        if len(fixed) > MAX_REPLACEMENT_CHARS:
            return None
        # Homophone guard: rewriting a single everyday word ("there" ->
        # "their") would misfire constantly — that fix is context-specific.
        if len(words) == 1 and _in_dictionary(words[0], self.system_dict):
            return None
        return (wrong, fixed)


# ──────────────────────────── macOS helpers ────────────────────────────


def play_sound(name, settings):
    if settings.get("play_sounds", True):
        subprocess.Popen(
            ["afplay", f"/System/Library/Sounds/{name}.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _osascript_quote(text):
    """Escape text for embedding in an AppleScript double-quoted string."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def show_notification(title, message):
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{_osascript_quote(message)}" '
         f'with title "{_osascript_quote(title)}"'],
        capture_output=True,
    )


def focused_element_is_editable():
    """Whether keyboard focus is in something that accepts typed text.

    Uses the Accessibility API: a real text field/area role counts, and so
    does any focused element whose value can be written. Returns True when
    it can't tell, so dictation behaves as before (types) rather than
    silently diverting to the clipboard.
    """
    try:
        from AppKit import NSWorkspace
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementIsAttributeSettable,
        )

        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None:
            return True
        app = AXUIElementCreateApplication(front.processIdentifier())
        err, focused = AXUIElementCopyAttributeValue(app, "AXFocusedUIElement", None)
        if err != 0 or focused is None:
            return False  # nothing focused at all — desktop, empty space
        err, role = AXUIElementCopyAttributeValue(focused, "AXRole", None)
        if err == 0 and role in (
            "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"
        ):
            return True
        err, settable = AXUIElementIsAttributeSettable(focused, "AXValue", None)
        return err == 0 and bool(settable)
    except Exception:
        return True


def _accessibility_granted():
    """Whether this process may synthesize keystrokes (Accessibility)."""
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return True  # can't check — try anyway


def _press_key(keycode, command=False):
    """Synthesize one keystroke via CoreGraphics.

    This posts the event from Flow Local itself, so it needs only the
    Accessibility permission — unlike osascript/System Events, which
    additionally needs the separate Automation permission.
    """
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGEventFlagMaskCommand,
        kCGHIDEventTap,
    )

    for is_down in (True, False):
        event = CGEventCreateKeyboardEvent(None, keycode, is_down)
        if command:
            CGEventSetFlags(event, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, event)


def paste_text(text, settings):
    """Insert text into the frontmost app via clipboard + Cmd-V.

    Returns the exact string inserted, or None if macOS would block the
    keystroke (missing Accessibility permission). The previous clipboard
    contents (plain text) are restored afterwards on success; on failure
    the text is left on the clipboard so you can paste it yourself.
    """
    if settings.get("append_space", True):
        text = text + " "

    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))
    if not _accessibility_granted():
        return None
    _press_key(9, command=True)  # keycode 9 = "v"
    # Give the paste a moment to land before restoring the clipboard
    time.sleep(0.4)
    subprocess.run(["pbcopy"], input=old)
    return text


def send_backspaces(count):
    """Press Delete `count` times in the frontmost app (for "scratch that")."""
    if not _accessibility_granted():
        return False
    for _ in range(min(count, 500)):  # safety cap
        _press_key(51)  # keycode 51 = delete
        time.sleep(0.005)  # keep the ordering reliable
    return True


def acquire_single_instance_lock():
    """Return a held lock if this is the only running Flow Local, else None.

    The returned file handle must stay referenced for the app's lifetime.
    """
    handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


# ──────────────────────────── Engine ───────────────────────────────────


class FlowEngine:
    """Records, transcribes, cleans, and types. UI-agnostic.

    A frontend (terminal or menu bar) reads these attributes:
      state          "loading" | "ready" | "recording" | "locked" |
                     "transcribing" | "downloading" | "error"
      error_message  reason when state == "error" (sticky until next success)
      history        newest-first list of dicts: text / words / seconds / when
      total_words, total_audio_seconds   session stats

    and may pass on_event(kind, message) to get notified of changes
    (called from background threads — don't touch UI directly in it).
    """

    HISTORY_LIMIT = 10

    def __init__(self, settings, on_event=None):
        import numpy as np
        import sounddevice as sd

        self.np = np
        self.sd = sd
        self.settings = settings
        self.on_event = on_event or (lambda kind, msg: None)

        self.state = "loading"
        self.state_detail = ""  # human-readable detail for the current state
        self.error_message = ""
        self.history = []
        self.history_version = 0  # bump so the UI knows to rebuild its menu
        self.total_words = 0
        self.total_audio_seconds = 0.0

        self.model = None
        self.model_size = settings["model_size"]
        self._model_lock = threading.Lock()

        self.frames = []
        self.recording = False
        self._frames_lock = threading.Lock()
        self.stream = None

        self._last_pasted = ""  # exact last insertion, for "scratch that"

        # Personal dictionary that improves with use
        self.learned = load_learned_words()
        self._system_dict = load_system_dictionary()

        # Corrections learned from backspace-and-retype edits
        self.edit_watcher = EditWatcher(self._system_dict, self._learn_correction)
        self.last_learned_correction = None
        self.correction_version = 0

        # (title, message) pairs for the frontend to display as
        # notifications — the menu bar app drains this on its UI timer so
        # notifications come from Flow Local's own identity.
        self.pending_notifications = []

        # Hotkey state machine
        self._mode = "idle"  # idle | hold | locked
        self._press_time = 0.0
        self._last_tap_time = -10.0
        self._ignore_release = False
        self._listener = None
        self._hotkey_name = None

    # ── events / state ──
    def _set_state(self, state, message=""):
        self.state = state
        self.state_detail = message
        if state == "error":
            self.error_message = message
        self.on_event("state", message)

    def _fail(self, message):
        self._set_state("error", message)

    def _clear_error(self):
        if self.error_message:
            self.error_message = ""

    # ── model ──
    def load_model(self):
        """Load (downloading if needed) the configured Whisper model. Blocking."""
        from faster_whisper import WhisperModel

        size = self.model_size
        with self._model_lock:
            self._set_state("downloading", f"Loading model {size}…")
            try:
                model = WhisperModel(size, device="cpu", compute_type="int8")
            except Exception as e:
                self._fail(f"Model '{size}' failed to load/download: {e}")
                return False
            self.model = model
        self._set_state("ready")
        return True

    def switch_model(self, size):
        """Switch models in the background; saves the choice to config.json."""
        if size == self.model_size and self.model is not None:
            return

        def worker():
            old_size = self.model_size
            self.model_size = size
            if self.load_model():
                self.settings["model_size"] = size
                save_settings(self.settings)
                self.on_event("log", f"Switched to {size}")
            else:
                self.model_size = old_size  # keep using the old model
                if self.model is not None:
                    self.state = "ready"

        threading.Thread(target=worker, daemon=True).start()

    # ── audio ──
    def open_microphone(self):
        """Open the input stream (shows macOS's orange mic indicator)."""
        if self.stream is not None:
            return True
        try:
            self.stream = self.sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._audio_callback,
            )
            self.stream.start()
            return True
        except Exception as e:
            self.stream = None
            self._fail(f"Microphone unavailable: {e}")
            return False

    def close_microphone(self):
        """Release the mic entirely (orange indicator turns off)."""
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _audio_callback(self, indata, frames, time_info, status):
        if self.recording:
            with self._frames_lock:
                self.frames.append(indata.copy())

    def _learn_correction(self, wrong, fixed):
        """An edit-derived fix becomes a visible rule in config.json."""
        self.settings.setdefault("corrections", {})[wrong] = fixed
        save_settings(self.settings)
        self.last_learned_correction = (wrong, fixed)
        self.correction_version += 1
        self.on_event("log", f'Learned correction: "{wrong}" → "{fixed}"')

    def _start_recording(self, locked=False):
        self.edit_watcher.flush(time.monotonic())
        if self.stream is None and not self.open_microphone():
            return
        with self._frames_lock:
            self.frames = []
        self.recording = True
        play_sound("Glass" if locked else "Pop", self.settings)
        self._set_state("locked" if locked else "recording")

    def _discard_recording(self):
        self.recording = False
        with self._frames_lock:
            self.frames = []
        if not self.settings.get("keep_mic_open", False):
            self.close_microphone()
        if self.state in ("recording", "locked"):
            self._set_state("ready")

    def _stop_recording(self):
        self.recording = False
        with self._frames_lock:
            frames = self.frames
            self.frames = []
        if not self.settings.get("keep_mic_open", False):
            self.close_microphone()
        if not frames:
            self._set_state("ready")
            return
        audio = self.np.concatenate(frames)[:, 0]
        duration = len(audio) / SAMPLE_RATE
        if duration < self.settings.get("min_seconds", 0.4):
            self.on_event("log", f"(ignored {duration:.1f}s tap)")
            self._set_state("ready")
            return
        play_sound("Bottle", self.settings)
        self._set_state("transcribing")
        threading.Thread(target=self._transcribe, args=(audio, duration), daemon=True).start()

    # ── transcription ──
    def _transcribe(self, audio, duration):
        if self.model is None:
            self._fail("Model not loaded yet — try again in a moment.")
            return
        t0 = time.time()
        language = "en" if self.model_size.endswith(".en") else None
        initial_prompt = build_initial_prompt(self.settings, self.learned)
        try:
            with self._model_lock:
                segments, _info = self.model.transcribe(
                    audio,
                    language=language,
                    vad_filter=True,
                    beam_size=5,
                    initial_prompt=initial_prompt,
                )
                raw = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as e:
            self._fail(f"Transcription failed: {e}")
            return

        text = clean_text(raw, self.settings)
        elapsed = time.time() - t0

        if not text:
            self.on_event("log", "(no speech detected)")
            self._set_state("ready")
            return

        if is_scratch_command(text):
            self._scratch_last()
            return

        text = tidy_spacing(apply_corrections(text, self.settings.get("corrections", {})))

        # Nowhere to type? Keep it on the clipboard and say so.
        if self.settings.get("copy_when_no_text_field", True) and not focused_element_is_editable():
            subprocess.run(["pbcopy"], input=text.encode("utf-8"))
            preview = text if len(text) <= 60 else text[:60] + "…"
            play_sound("Purr", self.settings)  # audible cue: it went to the clipboard
            self.pending_notifications.append(
                ("Copied to clipboard", f"“{preview}” — paste it anywhere with ⌘V")
            )
            self._last_pasted = ""  # nothing on screen for "scratch that"
            self._clear_error()
            self._remember(text, duration)
            if self.settings.get("auto_learn_vocabulary", True):
                learn_words(self.learned, text, self._system_dict, self.settings)
                save_learned_words(self.learned)
            self._set_state("ready")
            self.on_event("result", f'copied "{text}" to clipboard')
            return

        inserted = paste_text(text, self.settings)
        if inserted is None:
            self._fail(
                "macOS blocked the paste — enable Accessibility permission "
                "for Flow Local (or Terminal), then try again."
            )
            return

        self._last_pasted = inserted
        self._clear_error()
        self._remember(text, duration)
        if self.settings.get("auto_learn_vocabulary", True):
            learn_words(self.learned, text, self._system_dict, self.settings)
            save_learned_words(self.learned)
        if self.settings.get("learn_from_edits", True):
            self.edit_watcher.start(inserted, time.monotonic())
        self._set_state("ready")
        self.on_event(
            "result",
            f'"{text}"  [{duration:.1f}s audio, {elapsed:.1f}s to transcribe]',
        )

    def _remember(self, text, duration):
        words = len(text.split())
        self.history.insert(0, {"text": text, "words": words, "seconds": duration,
                                "when": time.strftime("%H:%M")})
        del self.history[self.HISTORY_LIMIT:]
        self.total_words += words
        self.total_audio_seconds += duration
        self.history_version += 1

    def _scratch_last(self):
        """Erase the previous dictation ("scratch that" voice command)."""
        if not self._last_pasted:
            self.on_event("log", "(nothing to scratch)")
            self._set_state("ready")
            return
        if send_backspaces(len(self._last_pasted)):
            self.on_event("log", "(scratched last dictation)")
            # It's gone from the screen, so drop it from history/stats too.
            if self.history:
                entry = self.history.pop(0)
                self.total_words -= entry["words"]
                self.total_audio_seconds -= entry["seconds"]
                self.history_version += 1
                # A scratched dictation was probably misheard — un-learn it.
                if self.settings.get("auto_learn_vocabulary", True):
                    unlearn_words(self.learned, entry["text"])
                    save_learned_words(self.learned)
            self._last_pasted = ""
            self._set_state("ready")
        else:
            self._fail("Could not send backspaces — check Accessibility permission.")

    def minutes_saved(self):
        """Typing time avoided (at typing_wpm) minus time spent speaking."""
        wpm = max(1, self.settings.get("typing_wpm", 40))
        saved = self.total_words / wpm - self.total_audio_seconds / 60.0
        return max(0.0, saved)

    # ── hotkey handling (hold-to-talk + double-tap toggle mode) ──
    def start_hotkey_listener(self):
        from pynput import keyboard

        name = self.settings.get("hotkey", "alt_r")
        hotkey = getattr(keyboard.Key, name, None)
        if hotkey is None:
            self._fail(f"Unknown hotkey '{name}' in config.json — see README.")
            return False

        def on_press(key):
            if key == hotkey:
                self._hotkey_pressed()

        def on_release(key):
            if key == hotkey:
                self._hotkey_released()

        if self._listener is not None:
            self._listener.stop()
        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()
        self._hotkey_name = name
        return True

    def _hotkey_pressed(self):
        now = time.monotonic()
        if self._mode == "locked":
            # Tap while locked: stop and transcribe.
            self._mode = "idle"
            self._ignore_release = True
            self._stop_recording()
        elif self._mode == "idle":
            if now - self._last_tap_time <= self.settings.get("double_tap_seconds", 0.5):
                # Second tap of a double-tap: lock recording on.
                self._mode = "locked"
                self._last_tap_time = -10.0
                self._start_recording(locked=True)
            else:
                self._mode = "hold"
                self._press_time = now
                self._start_recording()

    def _hotkey_released(self):
        if self._ignore_release:
            self._ignore_release = False
            return
        now = time.monotonic()
        if self._mode == "hold":
            self._mode = "idle"
            if now - self._press_time <= self.settings.get("tap_seconds", 0.3):
                # Just a tap — maybe the first half of a double-tap.
                self._last_tap_time = now
                self._discard_recording()
            else:
                self._stop_recording()
        # In locked mode the release of the locking tap is ignored.

    # ── settings ──
    def reload_settings(self):
        """Re-read config.json and apply what can change at runtime."""
        settings, error = load_settings()
        if error:
            self._fail(error)
            return False
        old_hotkey = self._hotkey_name
        old_model = self.model_size
        self.settings = settings
        self._clear_error()
        if settings.get("hotkey") != old_hotkey and self._listener is not None:
            self.start_hotkey_listener()
        # Apply a keep_mic_open change right away (unless mid-recording)
        if not self.recording:
            if settings.get("keep_mic_open", False):
                self.open_microphone()
            else:
                self.close_microphone()
        if settings["model_size"] != old_model:
            self.switch_model(settings["model_size"])
        elif self.state == "error":
            self._set_state("ready")
        self.on_event("log", "Settings reloaded")
        return True

    def shutdown(self):
        if self._listener is not None:
            self._listener.stop()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()


# ──────────────────────────── Terminal mode ────────────────────────────


def main():
    if sys.platform != "darwin":
        print("This script is written for macOS.")
        sys.exit(1)

    lock = acquire_single_instance_lock()
    if lock is None:
        print("Flow Local is already running — exiting.")
        sys.exit(0)

    settings, config_error = load_settings()
    if config_error:
        print(f"⚠️  {config_error} (using defaults)")

    def on_event(kind, message):
        if kind == "result":
            print(f"  → {message}")
        elif kind == "log":
            print(f"  {message}")
        elif kind == "state" and message:
            print(f"  {message}")

    engine = FlowEngine(settings, on_event=on_event)
    print(f"Loading Whisper model '{engine.model_size}' (first run downloads it)…")
    if not engine.load_model():
        print(f"Error: {engine.error_message}")
        sys.exit(1)
    if settings.get("keep_mic_open", False):
        engine.open_microphone()
    if not engine.start_hotkey_listener():
        print(f"Error: {engine.error_message}")
        sys.exit(1)

    print(
        f"\nReady. Hold [{settings['hotkey']}] and speak; release to type."
        f"\nDouble-tap to lock recording on; tap once to stop."
        f"\nCtrl-C here to quit.\n"
    )
    try:
        while True:
            time.sleep(1)
            while engine.pending_notifications:
                title, message = engine.pending_notifications.pop(0)
                print(f"  {title}: {message}")
            if engine.state == "error" and engine.error_message:
                print(f"⚠️  {engine.error_message}")
                engine.error_message = ""
    except KeyboardInterrupt:
        engine.shutdown()
        print("\nBye.")


if __name__ == "__main__":
    main()
