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
    # Forced fixes applied after transcription (case-insensitive match).
    # Example: {"cloud code": "Claude Code"}
    "corrections": {},
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


def clean_text(text, settings=None):
    """Rule-based cleanup: spoken punctuation, fillers, spacing, capitals."""
    s = settings if settings is not None else DEFAULT_SETTINGS

    # Normalize all whitespace first so phrase matching is predictable.
    text = re.sub(r"\s+", " ", text).strip()

    # Spoken punctuation: "comma" -> "," etc. Longest phrases first so
    # "question mark" wins before any shorter overlap. Newline values
    # become placeholders until the very end.
    punct = s.get("spoken_punctuation", {})
    for phrase in sorted(punct, key=len, reverse=True):
        symbol = punct[phrase].replace("\n", _NL)
        text = re.sub(r"\b" + re.escape(phrase) + r"\b", symbol, text, flags=re.IGNORECASE)

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


# ──────────────────────────── macOS helpers ────────────────────────────


def play_sound(name, settings):
    if settings.get("play_sounds", True):
        subprocess.Popen(
            ["afplay", f"/System/Library/Sounds/{name}.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def paste_text(text, settings):
    """Insert text into the frontmost app via clipboard + Cmd-V.

    Returns the exact string inserted, or None if macOS blocked the
    keystroke (missing Accessibility permission). The previous clipboard
    contents (plain text) are restored afterwards on success; on failure
    the text is left on the clipboard so you can paste it yourself.
    """
    if settings.get("append_space", True):
        text = text + " "

    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))
    result = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    # Give the paste a moment to land before restoring the clipboard
    time.sleep(0.4)
    subprocess.run(["pbcopy"], input=old)
    return text


def send_backspaces(count):
    """Press Delete `count` times in the frontmost app (for "scratch that")."""
    count = min(count, 500)  # safety cap
    result = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events"\n'
            f"repeat {count} times\n"
            "key code 51\n"
            "end repeat\n"
            "end tell",
        ],
        capture_output=True,
    )
    return result.returncode == 0


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
        """Keep the input stream open so recording starts instantly."""
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

    def _audio_callback(self, indata, frames, time_info, status):
        if self.recording:
            with self._frames_lock:
                self.frames.append(indata.copy())

    def _start_recording(self, locked=False):
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
        if self.state in ("recording", "locked"):
            self._set_state("ready")

    def _stop_recording(self):
        self.recording = False
        with self._frames_lock:
            frames = self.frames
            self.frames = []
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
        vocabulary = self.settings.get("vocabulary", [])
        initial_prompt = ("Vocabulary: " + ", ".join(vocabulary)) if vocabulary else None
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

        text = apply_corrections(text, self.settings.get("corrections", {}))

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
            if engine.state == "error" and engine.error_message:
                print(f"⚠️  {engine.error_message}")
                engine.error_message = ""
    except KeyboardInterrupt:
        engine.shutdown()
        print("\nBye.")


if __name__ == "__main__":
    main()
