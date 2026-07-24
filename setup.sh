#!/bin/bash
# One-time setup for Flow Local. Run:  bash setup.sh
set -e
cd "$(dirname "$0")"

echo "Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "Installing dependencies (this may take a minute)..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo ""
echo "Done. Next, build the menu bar app:"
echo ""
echo "    bash make_app.sh"
echo ""
echo "(or run terminal-only with: bash run.sh)"
echo ""
echo "The first run downloads the Whisper model (~480MB), then it's fully offline."
