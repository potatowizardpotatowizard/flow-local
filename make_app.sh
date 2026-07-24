#!/bin/bash
# Build "Flow Local.app" in ~/Applications and add it to Login Items.
# Run:  bash make_app.sh
#
# The .app is a tiny bash launcher that starts flow_menubar.py from this
# folder's .venv — no py2app, no bundling. Keeping the same app name and
# bundle id (local.flow.dictation) means macOS permissions (Microphone,
# Input Monitoring, Accessibility) stay granted across rebuilds.
set -e
cd "$(dirname "$0")"
SRC="$(pwd)"
APP="$HOME/Applications/Flow Local.app"

if [ ! -x "$SRC/.venv/bin/python" ]; then
    echo "No .venv found — run 'bash setup.sh' first."
    exit 1
fi

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# App icon (generated once; delete icon.icns to regenerate)
if [ ! -f "$SRC/icon.icns" ]; then
    "$SRC/.venv/bin/python" "$SRC/make_icon.py"
fi
cp "$SRC/icon.icns" "$APP/Contents/Resources/icon.icns"

# Info.plist — the app's identity. LSUIElement hides it from the Dock
# (it lives in the menu bar only).
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>Flow Local</string>
    <key>CFBundleDisplayName</key>     <string>Flow Local</string>
    <key>CFBundleIdentifier</key>      <string>local.flow.dictation</string>
    <key>CFBundleVersion</key>         <string>3.0</string>
    <key>CFBundleShortVersionString</key> <string>3.0</string>
    <key>CFBundleExecutable</key>      <string>flow-local</string>
    <key>CFBundleIconFile</key>        <string>icon</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>LSUIElement</key>             <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>Flow Local records your voice locally to turn it into text. Audio never leaves this Mac.</string>
</dict>
</plist>
PLIST

# The launcher: absolute paths baked in at build time.
cat > "$APP/Contents/MacOS/flow-local" <<LAUNCHER
#!/bin/bash
cd "$SRC"
exec "$SRC/.venv/bin/python" "$SRC/flow_menubar.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/flow-local"

# Refresh LaunchServices so Finder/login items see the (re)built app.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true

# Add to Login Items (idempotent — skip if already there). The in-app
# "Launch at Login" menu item can toggle this later.
if ! osascript -e 'tell application "System Events" to get the name of every login item' 2>/dev/null | grep -q "Flow Local"; then
    osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP\", hidden:false}" >/dev/null 2>&1 \
        && echo "Added to Login Items." \
        || echo "Couldn't add to Login Items automatically — use the in-app 'Launch at Login' toggle."
else
    echo "Already in Login Items."
fi

echo "Built: $APP"
echo "Launch it now with:  open \"$APP\""
