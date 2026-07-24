# Flow Local

**Offline dictation for macOS.** Hold a key, speak, release — your words are
transcribed on-device with Whisper, cleaned up, and typed into whatever app
is in front. Email, Slack, docs, code review comments, anywhere.

- 🔒 **Fully private** — audio never leaves your Mac. No cloud, no account,
  no API key, no subscription, no telemetry. Unplug your network and it
  still works.
- 🎙 **Lives in the menu bar** — hold **Right Option** to talk, or
  double-tap it to lock recording on hands-free.
- 🧹 **Cleans as it types** — strips "um"/"uh", collapses stutters, converts
  spoken punctuation ("comma", "period", "new paragraph"), fixes
  capitalization, applies your personal auto-corrections.
- 🗣 **Knows your words** — teach it names and jargon in a personal
  dictionary, and it also **learns automatically**: unusual words you
  dictate are remembered (locally, in `learned_words.json`) and hinted
  back to Whisper, so recognition of *your* vocabulary improves with use.
- ↩️ **"Scratch that"** — say it to erase the last dictation.
- ✏️ **Learns from your edits** — backspace part of a fresh dictation and
  retype it (say "dot dot dot", replace the literal words with `...`) and
  that fix becomes an automatic rule from then on. You get a notification,
  and the rule is visible in `config.json` under `corrections`.

Total footprint is about 1 GB, mostly the Whisper model.

## Install (from a fresh clone, ~5 minutes)

Requires macOS and Python 3.9+ (`xcode-select --install` if macOS asks for
developer tools).

```bash
git clone <your-fork-or-this-repo> ~/flow-local
cd ~/flow-local
bash setup.sh      # creates .venv and installs dependencies
bash make_app.sh   # builds "Flow Local.app" in ~/Applications + Login Items
open ~/Applications/"Flow Local.app"
```

The first launch downloads the default Whisper model (~480 MB); after that
it is fully offline. A 🎙 icon appears in the menu bar when it's ready.

Prefer no app bundle? `bash run.sh` runs the same engine in a terminal.

### Permissions walkthrough (one time)

Flow Local needs exactly two macOS permissions, and the app walks you
through them. Here's what to expect on first launch, in order:

1. **Accessibility** — lets Flow Local type text into other apps. The
   first launch pops up macOS's own dialog: click **Open System
   Settings**, find **Flow Local** in the Accessibility list, and switch
   it on (macOS asks for your password/Touch ID). Missed the dialog? Go
   to **System Settings → Privacy & Security → Accessibility** manually.
2. **Restart the app once.** Permissions only apply to a freshly started
   app: click the menu bar **🎙 → Quit Flow Local**, then reopen it from
   Spotlight. This step is not optional — skipping it is the #1 reason
   for "it transcribes but nothing gets typed."
3. **Microphone** — macOS asks the first time you actually record
   (hold the hotkey and speak). Click Allow.

Good to know, so nothing surprises you:

- Opening the app shows **no window and no Dock icon** — it lives
  entirely in the menu bar (the 🎙 at the top-right of your screen).
  Opening it a second time just shows an "already running" notification.
- The **orange microphone indicator** appears only while you're actually
  dictating — the mic is opened when you press the hotkey and fully
  released when you stop. (If you'd rather shave ~0.2s off the recording
  start, set `keep_mic_open` to `true`; the indicator then stays on, but
  audio is still discarded except while recording.)
- If the menu bar icon shows **⚠️**, click it — the first menu line
  states the exact problem, and it's almost always the Accessibility
  permission or the missing restart from step 2.
- `make_app.sh` always builds with the same name and bundle id
  (`local.flow.dictation`), so rebuilding or updating never resets your
  permissions.
- Terminal mode (`run.sh`) is separate: it runs under **Terminal's**
  permissions instead (Microphone + Accessibility + Input Monitoring
  for its hotkey listener).

## Using it

| Action | How |
|---|---|
| Dictate | Hold **Right Option**, speak, release |
| Hands-free mode | **Double-tap** Right Option to lock recording on (icon shows 🔴🔒); tap once to stop and transcribe |
| Erase last dictation | Say **"scratch that"** (or "delete that") |
| Spoken punctuation | Say "comma", "period", "question mark", "new line", "new paragraph", … |
| Re-copy an old dictation | Menu bar 🎙 → **History** → click an entry (copies to clipboard) |
| Switch model | Menu bar 🎙 → **Model** (downloads on first use, no restart needed) |
| Change settings | Menu bar 🎙 → **Open Settings file**, edit, then **Reload settings** |
| Start at login | Menu bar 🎙 → **Launch at Login** |

Menu bar icon states: 🎙 ready · 🔴 recording · 🔴🔒 locked recording ·
💬 transcribing · ⏳ loading a model · ⚠️ error (the reason appears as the
first menu item).

The **Session** line in the menu shows words dictated and roughly how many
minutes of typing you saved (against a 40 wpm typing speed — set
`typing_wpm` to yours).

## Settings reference (`config.json`)

All settings live in `config.json` next to the scripts, created with these
defaults on first run. Edit it (menu → Open Settings file), then use
**Reload settings** — no restart needed.

| Key | Default | What it does |
|---|---|---|
| `hotkey` | `"alt_r"` | Push-to-talk key: `alt_r`, `alt_l`, `cmd_r`, `ctrl_r`, or `f13`–`f20`. |
| `model_size` | `"small.en"` | Whisper model: `tiny.en` (fastest) → `medium.en` (most accurate). Use `small` (no `.en`) for non-English. |
| `play_sounds` | `true` | Soft pop/click when recording starts, locks, and stops. |
| `keep_mic_open` | `false` | `false`: mic is on only while recording (orange indicator = actually listening). `true`: keep the stream open for instant recording start. |
| `append_space` | `true` | Add a trailing space after each dictation. |
| `min_seconds` | `0.4` | Ignore recordings shorter than this (accidental taps). |
| `double_tap_seconds` | `0.5` | Two taps within this window lock recording on. |
| `tap_seconds` | `0.3` | A press shorter than this counts as a tap, not a hold. |
| `typing_wpm` | `40` | Your typing speed, for the "minutes saved" stat. |
| `fillers` | `["um", "uh", …]` | Words to strip. Each also matches with a stretched last letter ("ummm"). |
| `spoken_punctuation` | `{"comma": ",", …}` | Say the key, get the value. Add your own (`"smiley": "🙂"`). Values may contain `\n`. |
| `spoken_punctuation_mode` | `"smart"` | `smart` converts spoken punctuation but leaves obvious noun uses alone ("the trial period ends", "an Oxford comma"). `always` converts every occurrence; `off` disables conversion. |
| `vocabulary` | `[]` | Names/jargon hinted to Whisper so it recognizes them, e.g. `["Benjiman", "kubectl"]`. |
| `auto_learn_vocabulary` | `true` | Mine each dictation for unusual words (not in the system dictionary) and remember them in `learned_words.json`. Words seen twice get hinted to Whisper alongside `vocabulary`; "scratch that" un-learns the erased text. Set `false` to only use the manual list. |
| `learn_from_edits` | `true` | Watch the ~30s after each dictation for backspace-and-retype fixes and turn them into `corrections` rules (with a notification). Guarded: clicks, arrow keys, and app switches cancel it; long rephrasings and single common-word swaps ("there" → "their") are never learned. Menu bar app only. |
| `corrections` | `{}` | Forced post-fixes, e.g. `{"cloud code": "Claude Code"}`. Case-insensitive, whole words. |

`config.json` and `learned_words.json` are `.gitignore`d because they
accumulate personal names and vocabulary; a fresh clone regenerates
defaults and starts learning from scratch. Both live in this folder, never
leave your Mac, and can be opened, edited, or deleted anytime.

## Troubleshooting

- **⚠️ in the menu bar** — click it; the first menu line says what went
  wrong (mic unavailable, model download failed, paste blocked, broken
  config.json).
- **Nothing types, but History shows the text** — Accessibility permission
  is missing, or the app needs one restart after you granted it.
- **Hotkey does nothing** — Accessibility permission (Input Monitoring for
  terminal mode). Also check `hotkey` in config.json is a supported name.
- **"no speech detected"** — Microphone permission, or the wrong input
  device in System Settings → Sound → Input.
- **Slow transcription** — switch to `base.en` or `tiny.en` in the Model
  menu.
- **It typed "period" literally (or turned it into a ".") when you meant
  the opposite** — the default `smart` mode guesses from context: a
  determiner right before the word ("in **a** period", "**the** trial
  period ends") keeps it literal, everything else converts. It can guess
  wrong on unusual phrasing. Set `spoken_punctuation_mode` to `always` or
  `off` to remove the guessing, or delete individual entries from
  `spoken_punctuation`.
- **It learned a correction I don't want** — open the settings file and
  delete that entry from `corrections`, then Reload settings. Set
  `learn_from_edits` to `false` to turn the feature off entirely.
- **A privacy note on edit-learning** — keystrokes are only examined for a
  short window right after Flow Local itself pastes text, only to detect a
  backspace-and-retype of that text; they are never logged or stored (the
  only thing kept is the finished correction rule you can see in
  `config.json`). Everything is local, and the code is ~100 readable lines
  (`EditWatcher` in `flow_local.py`) if you want to check.
- **Clipboard** — text is inserted via the clipboard; your previous
  clipboard *text* is restored afterwards (images/files are not).
- **Two icons / double-typed text** — can't happen anymore: a second launch
  exits quietly (single-instance lock).

## How it compares to Wispr Flow

> Flow Local is an independent open-source project. It is not affiliated
> with, endorsed by, or connected to Wispr Inc. (makers of Wispr Flow) or
> OpenAI (creators of the Whisper speech model it runs locally).

Honest version: [Wispr Flow](https://wisprflow.ai) is a polished commercial
product with cloud-scale accuracy, AI rewriting/tone matching, context
awareness across apps, team features, and support. Flow Local is a few
hundred lines of Python.

What you get here instead:

- **Privacy**: your audio and text never leave the machine. Wispr Flow
  processes audio in the cloud.
- **Price**: free and MIT-licensed vs. a subscription.
- **Offline**: works on a plane.
- **Hackable**: every behavior is a readable Python function or a
  config.json key.

What you give up: accuracy beyond what local Whisper models offer (medium.en
is good, not magical), AI-powered rewriting/formatting, per-app tone,
multi-language auto-detection, mobile keyboards, and someone to email when
it breaks. If dictation is mission-critical for you, try both.

## Uninstalling

Quit the app, then:

```bash
rm -rf ~/Applications/"Flow Local.app" ~/flow-local
rm -rf ~/.cache/huggingface/hub   # the downloaded Whisper models
```

and remove "Flow Local" from Login Items if you enabled it.

## Development

```bash
.venv/bin/python -m pytest        # run the test suite
```

`flow_local.py` is the engine (audio, Whisper, text cleanup, hotkey state
machine); `flow_menubar.py` is the menu bar UI on top of it; `make_app.sh`
wraps the latter in a minimal .app bundle. Tests cover the text pipeline
and config handling — everything that doesn't need a microphone.

## License

MIT — see [LICENSE](LICENSE).
