# Flow Local

Hold-to-talk dictation for macOS — fully offline, no accounts, no subscription.

Hold **Right Option**, speak, release. Your words are transcribed locally with
Whisper, cleaned up (filler words removed, punctuation fixed), and typed into
whatever app is in front — email, Slack, docs, anywhere.

Total disk footprint: about 1GB (mostly the Whisper model). Nothing ever
leaves your Mac.

## Setup (one time, ~5 minutes)

1. Put this folder somewhere permanent (e.g. `~/flow-local`).

2. Open Terminal, then run:

   ```
   cd ~/flow-local
   bash setup.sh
   ```

   If macOS says developer tools are missing, run `xcode-select --install`
   first, then re-run the setup.

3. Start it:

   ```
   bash run.sh
   ```

   The first run downloads the Whisper model (~480MB). After that it works
   with no internet at all.

4. **Grant permissions.** macOS will prompt you the first time; if it doesn't,
   go to **System Settings → Privacy & Security** and enable **Terminal** under
   each of these three sections:

   - **Microphone** — to hear you
   - **Input Monitoring** — to detect the hotkey
   - **Accessibility** — to type the text into other apps

   After granting Accessibility or Input Monitoring you may need to quit and
   reopen Terminal once.

## Using it

Keep the Terminal window running (minimize it if you like). In any app:

- **Hold Right Option** and speak.
- **Release** — a moment later the text appears where your cursor is.

You'll hear a soft pop when recording starts and a click when it stops.
Quit with Ctrl-C in the Terminal window.

## Tweaking

Open `flow_local.py` — all settings are at the top:

| Setting | What it does |
|---|---|
| `MODEL_SIZE` | `tiny.en` (fastest) → `medium.en` (most accurate). Default `small.en` is the sweet spot. Use `small` (no `.en`) for non-English. |
| `HOTKEY` | Which key to hold. Default `alt_r` (Right Option). |
| `APPEND_SPACE` | Add a trailing space after each dictation. |
| `PLAY_SOUNDS` | Start/stop sounds on or off. |
| `FILLER_PATTERN` | Which filler words get stripped. |

## Troubleshooting

- **Nothing types, but the transcript shows in Terminal** → Accessibility
  permission is missing (or Terminal needs a restart after granting it).
- **Hotkey does nothing** → Input Monitoring permission is missing.
- **"no speech detected"** → Microphone permission is missing, or the wrong
  input device is selected in System Settings → Sound → Input.
- **Transcription feels slow** → switch `MODEL_SIZE` to `base.en` or `tiny.en`.
- **Clipboard note** → the script pastes via the clipboard and restores your
  previous clipboard text afterwards. Images/files on the clipboard are not
  restored — only plain text.

## Uninstalling

Delete the folder, and delete the downloaded model at
`~/.cache/huggingface/hub` if you want the ~480MB back. That's it.
