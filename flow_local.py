#!/usr/bin/env python3
"""
Flow Local — hold-to-talk dictation for macOS, fully offline.

Hold the hotkey (Right Option by default), speak, release.
Your words are transcribed locally with Whisper, cleaned up,
and typed into whatever app is in front.

No cloud. No API keys. No subscription.
"""

import re
import subprocess
import sys
import threading
import time

# ──────────────────────────── Configuration ────────────────────────────

# Whisper model: tiny.en (~75MB, fastest) | base.en (~145MB) |
#                small.en (~480MB, recommended) | medium.en (~1.5GB, best)
# Use "small" (no .en) if you dictate in languages other than English.
MODEL_SIZE = "small.en"

# Hotkey to hold while talking. Options: "alt_r" (Right Option),
# "alt_l" (Left Option), "cmd_r" (Right Command), "ctrl_r" (Right Control),
# "f13" ... "f20" (if your keyboard has them).
HOTKEY = "alt_r"

# Ignore recordings shorter than this (accidental taps), in seconds.
MIN_SECONDS = 0.4

# Append a trailing space after pasted text (like Wispr Flow does).
APPEND_SPACE = True

# Play a subtle sound when recording starts/stops.
PLAY_SOUNDS = True

# Filler words to strip. Tweak freely.
FILLER_PATTERN = r"\b(?:um+|uh+|uhm+|erm+|hmm+|mm-hmm|mmm+)\b"

SAMPLE_RATE = 16000

# ──────────────────────────── Text cleanup ─────────────────────────────


def clean_text(text: str) -> str:
    """Rule-based cleanup: strip fillers, fix spacing and capitalization."""
    # Remove filler words along with the commas that set them off
    # ("we should, um, move" -> "we should move")
    text = re.sub(
        r"(?:[,;:]\s*)?" + FILLER_PATTERN + r"\s*[,;:]?",
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

    # Strip leading orphaned punctuation
    text = re.sub(r"^[\s,.;:!?]+", "", text)

    text = text.strip()

    # Capitalize the first letter of the text and of each sentence
    def _cap(match):
        return match.group(1) + match.group(2).upper()

    if text:
        text = text[0].upper() + text[1:]
        text = re.sub(r"([.!?]\s+)([a-z])", _cap, text)

    return text


# ──────────────────────────── macOS helpers ────────────────────────────


def play_sound(name: str) -> None:
    if PLAY_SOUNDS:
        subprocess.Popen(
            ["afplay", f"/System/Library/Sounds/{name}.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def paste_text(text: str) -> None:
    """Insert text into the frontmost app via clipboard + Cmd-V.

    The previous clipboard contents (plain text) are restored afterwards.
    """
    if APPEND_SPACE:
        text = text + " "

    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))
    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ],
        capture_output=True,
    )
    # Give the paste a moment to land before restoring the clipboard
    time.sleep(0.4)
    subprocess.run(["pbcopy"], input=old)


# ──────────────────────────── Main app ─────────────────────────────────


class FlowLocal:
    def __init__(self):
        import numpy as np  # noqa: F401  (kept local so cleanup is testable without deps)
        import sounddevice as sd
        from faster_whisper import WhisperModel

        self.np = np
        self.sd = sd

        print(f"Loading Whisper model '{MODEL_SIZE}' (first run downloads it)...")
        self.model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        print("Model loaded.")

        self.frames = []
        self.recording = False
        self.lock = threading.Lock()

        # Keep the input stream open the whole time so recording starts instantly.
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
        )
        self.stream.start()

    # ── audio ──
    def _audio_callback(self, indata, frames, time_info, status):
        if self.recording:
            with self.lock:
                self.frames.append(indata.copy())

    def start_recording(self):
        with self.lock:
            self.frames = []
        self.recording = True
        play_sound("Pop")
        print("● recording... (release key to transcribe)")

    def stop_recording(self):
        self.recording = False
        with self.lock:
            frames = self.frames
            self.frames = []
        if not frames:
            return
        audio = self.np.concatenate(frames)[:, 0]
        duration = len(audio) / SAMPLE_RATE
        if duration < MIN_SECONDS:
            print(f"  (ignored {duration:.1f}s tap)")
            return
        play_sound("Bottle")
        threading.Thread(target=self._transcribe, args=(audio, duration), daemon=True).start()

    # ── transcription ──
    def _transcribe(self, audio, duration):
        t0 = time.time()
        language = None if not MODEL_SIZE.endswith(".en") else "en"
        segments, _info = self.model.transcribe(
            audio,
            language=language,
            vad_filter=True,
            beam_size=5,
        )
        raw = " ".join(seg.text.strip() for seg in segments).strip()
        text = clean_text(raw)
        elapsed = time.time() - t0

        if not text:
            print("  (no speech detected)")
            return

        paste_text(text)
        print(f'  → "{text}"  [{duration:.1f}s audio, {elapsed:.1f}s to transcribe]')

    # ── hotkey ──
    def run(self):
        from pynput import keyboard

        hotkey = getattr(keyboard.Key, HOTKEY, None)
        if hotkey is None:
            print(f"Unknown HOTKEY '{HOTKEY}' — see comments at top of script.")
            sys.exit(1)

        def on_press(key):
            if key == hotkey and not self.recording:
                self.start_recording()

        def on_release(key):
            if key == hotkey and self.recording:
                self.stop_recording()

        print(f"\nReady. Hold [{HOTKEY}] and speak; release to type. Ctrl-C here to quit.\n")
        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()


def main():
    if sys.platform != "darwin":
        print("This script is written for macOS.")
        sys.exit(1)
    try:
        app = FlowLocal()
        app.run()
    except KeyboardInterrupt:
        print("\nBye.")


if __name__ == "__main__":
    main()
