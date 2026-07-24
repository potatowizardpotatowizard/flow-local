#!/usr/bin/env python3
"""
Flow Local - menu bar app.

Wraps the engine in flow_local.py with a macOS menu bar icon:

    🎙  ready          🔴  recording (hold)     🔴🔒  recording locked on
    💬  transcribing   ⏳  loading a model      ⚠️  something went wrong

Plus: dictation history, session stats, model picker, settings file
access, and a launch-at-login toggle.  Built into "Flow Local.app" by
make_app.sh so macOS permissions stick to one identity.
"""

import os
import subprocess
import sys
import threading
import time

import rumps
from ApplicationServices import (
    AXIsProcessTrustedWithOptions,
    kAXTrustedCheckOptionPrompt,
)
from AppKit import (
    NSEvent,
    NSEventMaskFlagsChanged,
    NSEventMaskKeyDown,
    NSEventMaskKeyUp,
    NSEventMaskLeftMouseDown,
    NSEventMaskRightMouseDown,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventTypeFlagsChanged,
    NSEventTypeKeyDown,
    NSEventTypeKeyUp,
    NSEventTypeLeftMouseDown,
    NSEventTypeRightMouseDown,
)

import flow_local as fl

APP_BUNDLE_PATH = os.path.expanduser("~/Applications/Flow Local.app")

# The hotkey is watched with a native NSEvent global monitor rather than
# pynput: pynput's listener thread calls Text Services APIs that crash
# (dispatch_assert_queue) inside an AppKit app on modern macOS. The
# monitor is installed on the main thread and only needs the
# Accessibility permission the app already has for pasting.
KEYCODES = {
    "alt_r": 61, "alt_l": 58, "cmd_r": 54, "ctrl_r": 62,
    "f13": 105, "f14": 107, "f15": 113, "f16": 106,
    "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}
# Modifier keys arrive as flags-changed events; this maps their keycode to
# the flag that tells press apart from release.
MODIFIER_FLAGS = {
    61: NSEventModifierFlagOption,
    58: NSEventModifierFlagOption,
    54: NSEventModifierFlagCommand,
    62: NSEventModifierFlagControl,
}

ICONS = {
    "loading": "⏳",
    "downloading": "⏳",
    "ready": "🎙",
    "recording": "🔴",
    "locked": "🔴🔒",
    "transcribing": "💬",
    "error": "⚠️",
}

MODELS = ["tiny.en", "base.en", "small.en", "medium.en"]

HOTKEY_LABELS = {
    "alt_r": "Right Option",
    "alt_l": "Left Option",
    "cmd_r": "Right Command",
    "ctrl_r": "Right Control",
}


def hotkey_label(name):
    return HOTKEY_LABELS.get(name, name)


def copy_to_clipboard(text):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))


# ── Toast banners ───────────────────────────────────────────────────────
#
# Notification Center won't reliably show notifications for a
# script-launcher app (the permission prompt never even appears), so we
# draw our own: a small dark banner near the menu bar. Needs no
# permissions and ignores Focus modes. Main thread only.

_toasts = []  # [(panel, expires_at)]


def show_toast(title, message="", seconds=3.5):
    from AppKit import (
        NSBackingStoreBuffered,
        NSColor,
        NSFont,
        NSMakeRect,
        NSPanel,
        NSScreen,
        NSStatusWindowLevel,
        NSTextField,
        NSWindowStyleMaskBorderless,
        NSWindowStyleMaskNonactivatingPanel,
    )

    text = f"{title}\n{message}" if message else title
    label = NSTextField.wrappingLabelWithString_(text)
    label.setFont_(NSFont.systemFontOfSize_(13.0))
    label.setTextColor_(NSColor.whiteColor())
    label.setPreferredMaxLayoutWidth_(320.0)
    size = label.fittingSize()

    pad = 14.0
    width, height = size.width + 2 * pad, size.height + 2 * pad
    screen = NSScreen.mainScreen() or NSScreen.screens()[0]
    frame = screen.visibleFrame()
    x = frame.origin.x + frame.size.width - width - 16.0
    y = frame.origin.y + frame.size.height - height - 16.0

    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, y, width, height),
        NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
        NSBackingStoreBuffered,
        False,
    )
    panel.setReleasedWhenClosed_(False)
    panel.setLevel_(NSStatusWindowLevel)  # above normal windows
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    content = panel.contentView()
    content.setWantsLayer_(True)
    content.layer().setBackgroundColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.13, 0.13, 0.16, 0.94).CGColor()
    )
    content.layer().setCornerRadius_(12.0)
    label.setFrame_(NSMakeRect(pad, pad, size.width, size.height))
    content.addSubview_(label)
    panel.orderFrontRegardless()  # show without stealing keyboard focus

    _toasts.append((panel, time.monotonic() + seconds))
    return panel


def prune_toasts():
    now = time.monotonic()
    for entry in _toasts[:]:
        panel, expires_at = entry
        if now >= expires_at:
            panel.orderOut_(None)
            _toasts.remove(entry)


# ── Login item helpers (osascript + System Events) ──────────────────────

def login_item_exists():
    result = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to get the name of every login item'],
        capture_output=True, text=True,
    )
    return "Flow Local" in result.stdout


def add_login_item():
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to make login item at end with '
         f'properties {{path:"{APP_BUNDLE_PATH}", hidden:false}}'],
        capture_output=True,
    )


def remove_login_item():
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to delete login item "Flow Local"'],
        capture_output=True,
    )


# ── The app ─────────────────────────────────────────────────────────────


class FlowMenuBarApp(rumps.App):
    def __init__(self, engine, config_error=None):
        super().__init__("Flow Local", title="⏳", quit_button=None)
        self.engine = engine
        self.config_error = config_error
        self._shown_history_version = -1
        self._shown_correction_version = 0
        self._last_status = None

        # Menu skeleton. Items without a callback render greyed out,
        # which is what we want for the status and stats lines.
        self.status_item = rumps.MenuItem("Starting…")
        self.history_menu = rumps.MenuItem("History")
        self.history_menu.add(rumps.MenuItem("(empty)"))
        self.stats_item = rumps.MenuItem("Session: 0 words")

        self.model_menu = rumps.MenuItem("Model")
        for size in MODELS:
            item = rumps.MenuItem(size, callback=self.on_pick_model)
            self.model_menu.add(item)

        self.login_item = rumps.MenuItem("Launch at Login", callback=self.on_toggle_login)

        self.menu = [
            self.status_item,
            None,
            self.history_menu,
            self.stats_item,
            None,
            self.model_menu,
            None,
            rumps.MenuItem("Open Settings file", callback=self.on_open_settings),
            rumps.MenuItem("Reload settings", callback=self.on_reload_settings),
            self.login_item,
            None,
            rumps.MenuItem("Restart Flow Local", callback=self.on_restart),
            rumps.MenuItem("Quit Flow Local", callback=self.on_quit),
        ]

        self.login_item.state = 1 if login_item_exists() else 0
        self._sync_model_checkmarks()

        self._monitor = None
        self._hotkey_keycode = None
        self.install_hotkey_monitor()

        # The engine runs in background threads; this timer is the only
        # thing that touches the UI, always from the main thread.
        self.timer = rumps.Timer(self.sync_ui, 0.3)
        self.timer.start()

        # Load the model without blocking the menu bar.
        threading.Thread(target=self._start_engine, daemon=True).start()

    def _start_engine(self):
        if self.engine.load_model():
            if self.engine.settings.get("keep_mic_open", False):
                self.engine.open_microphone()
        # Surface a broken config.json even though the model loaded fine.
        if self.config_error:
            self.engine._fail(self.config_error)

    # ── hotkey (NSEvent global monitor) ──
    def install_hotkey_monitor(self):
        if self._monitor is not None:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None
        name = self.engine.settings.get("hotkey", "alt_r")
        keycode = KEYCODES.get(name)
        if keycode is None:
            self.engine._fail(
                f"Unknown hotkey '{name}' in config.json - "
                f"pick one of: {', '.join(KEYCODES)}"
            )
            return
        self._hotkey_keycode = keycode

        def handler(event):
            try:
                etype = event.type()
                if etype in (NSEventTypeLeftMouseDown, NSEventTypeRightMouseDown):
                    # A click moves the cursor - any pending edit-learning
                    # would attribute keystrokes to the wrong place.
                    self.engine.edit_watcher.abort()
                    return
                if event.keyCode() == self._hotkey_keycode:
                    if etype == NSEventTypeFlagsChanged:
                        # Modifier key: the flag tells press from release.
                        if event.modifierFlags() & MODIFIER_FLAGS[self._hotkey_keycode]:
                            self.engine._hotkey_pressed()
                        else:
                            self.engine._hotkey_released()
                    elif etype == NSEventTypeKeyDown and not event.isARepeat():
                        self.engine._hotkey_pressed()
                    elif etype == NSEventTypeKeyUp:
                        self.engine._hotkey_released()
                elif etype == NSEventTypeKeyDown and self.engine.settings.get(
                    "learn_from_edits", True
                ):
                    try:
                        chars = event.characters() or ""
                    except Exception:
                        chars = ""
                    has_command = bool(
                        event.modifierFlags()
                        & (NSEventModifierFlagCommand | NSEventModifierFlagControl)
                    )
                    self.engine.edit_watcher.observe_key(
                        event.keyCode(), chars, has_command, time.monotonic()
                    )
            except Exception as e:
                self.engine._fail(f"Hotkey handler error: {e}")

        mask = (
            NSEventMaskFlagsChanged
            | NSEventMaskKeyDown
            | NSEventMaskKeyUp
            | NSEventMaskLeftMouseDown
            | NSEventMaskRightMouseDown
        )
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask, handler
        )

    # ── periodic UI refresh ──
    def sync_ui(self, _timer):
        e = self.engine
        self.title = ICONS.get(e.state, "🎙")

        # Show queued engine notifications as our own toast banners
        # (Notification Center never even prompts for script-launcher apps).
        prune_toasts()
        while e.pending_notifications:
            title, message = e.pending_notifications.pop(0)
            show_toast(title, message)

        # A typing pause completes any pending edit-learning; announce
        # newly learned corrections so nothing happens silently.
        e.edit_watcher.tick(time.monotonic())
        if e.correction_version != self._shown_correction_version:
            self._shown_correction_version = e.correction_version
            if e.last_learned_correction:
                wrong, fixed = e.last_learned_correction
                show_toast(
                    "Learned a correction",
                    f'"{wrong}" will now become "{fixed}"',
                )

        if e.state == "error":
            status = "⚠️ " + (e.error_message or "Unknown error")
        elif e.state in ("loading", "downloading"):
            status = e.state_detail or "Loading model…"
        elif e.state == "recording":
            status = "Recording… release to type"
        elif e.state == "locked":
            status = "Recording locked - tap hotkey to stop"
        elif e.state == "transcribing":
            status = "Transcribing…"
        else:
            status = (
                f"Ready - hold {hotkey_label(e.settings.get('hotkey', 'alt_r'))} "
                f"to talk, double-tap to lock"
            )
        if len(status) > 90:
            status = status[:90] + "…"
        if status != self._last_status:
            self.status_item.title = status
            self._last_status = status

        minutes = e.minutes_saved()
        stats = f"Session: {e.total_words} words · ~{minutes:.1f} min of typing saved"
        if e.settings.get("auto_learn_vocabulary", True):
            stats += f" · {len(e.learned)} learned words"
        self.stats_item.title = stats

        if e.history_version != self._shown_history_version:
            self._rebuild_history()
            self._shown_history_version = e.history_version

        # Keep the model checkmark honest (it can change via Reload settings)
        self._sync_model_checkmarks()

    def _rebuild_history(self):
        self.history_menu.clear()
        if not self.engine.history:
            self.history_menu.add(rumps.MenuItem("(empty)"))
            return
        for entry in self.engine.history:
            label = entry["text"].replace("\n", " ")
            if len(label) > 55:
                label = label[:55] + "…"
            item = rumps.MenuItem(
                f'{entry["when"]}  “{label}”',
                callback=self._make_history_callback(entry["text"]),
            )
            self.history_menu.add(item)

    def _make_history_callback(self, text):
        def copy(_item):
            copy_to_clipboard(text)
        return copy

    def _sync_model_checkmarks(self):
        for size in MODELS:
            item = self.model_menu[size]
            item.state = 1 if size == self.engine.model_size else 0
            if self.engine.state == "downloading" and size == self.engine.model_size:
                item.title = f"{size}  (downloading…)"
            elif item.title != size:
                item.title = size

    # ── menu actions ──
    def on_pick_model(self, item):
        size = item.title.split()[0]  # strip any "(downloading…)" suffix
        self.engine.switch_model(size)

    def on_open_settings(self, _item):
        subprocess.run(["open", "-e", fl.CONFIG_PATH])

    def on_reload_settings(self, _item):
        if self.engine.reload_settings():
            self.install_hotkey_monitor()  # in case the hotkey changed

    def on_toggle_login(self, item):
        if item.state:
            remove_login_item()
        else:
            if not fl.os.path.exists(APP_BUNDLE_PATH):
                rumps.alert(
                    "Flow Local.app not found",
                    "Run `bash make_app.sh` first to build the app in "
                    "~/Applications, then try again.",
                )
                return
            add_login_item()
        item.state = 1 if login_item_exists() else 0

    def on_restart(self, _item):
        """Quit and come back fresh - the cure for a glitched session.

        A detached helper waits for this process to exit (releasing the
        single-instance lock) and then launches a new one.
        """
        self.engine.shutdown()
        if os.path.exists(APP_BUNDLE_PATH):
            subprocess.Popen(
                ["/bin/bash", "-c", 'sleep 1; open "$0"', APP_BUNDLE_PATH],
                start_new_session=True,
            )
        else:
            # Running straight from the terminal (no .app built yet)
            subprocess.Popen(
                ["/bin/bash", "-c", 'sleep 1; exec "$0" "$1"',
                 sys.executable, os.path.abspath(__file__)],
                start_new_session=True,
            )
        rumps.quit_application()

    def on_quit(self, _item):
        self.engine.shutdown()
        rumps.quit_application()


def main():
    if sys.platform != "darwin":
        print("Flow Local is macOS-only.")
        sys.exit(1)

    # Single-instance guard: a second launch exits instead of double-typing
    # every dictation - but says so, since a menu-bar app opening "does
    # nothing" visible. The handle must outlive the app.
    lock = fl.acquire_single_instance_lock()
    if lock is None:
        # Show the toast, give it a moment on screen, then bow out.
        from Foundation import NSDate, NSRunLoop

        show_toast(
            "Flow Local is already running",
            "Look for the 🎙 icon at the top-right of your screen.",
        )
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(3.0)
        )
        sys.exit(0)

    # Without Accessibility, dictations transcribe but can't be typed.
    # This shows macOS's own grant dialog on first run and adds Flow Local
    # to the Accessibility list (System Settings > Privacy & Security).
    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})

    settings, config_error = fl.load_settings()
    engine = fl.FlowEngine(settings)
    app = FlowMenuBarApp(engine, config_error)
    app.run()


if __name__ == "__main__":
    main()
