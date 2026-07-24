#!/bin/bash
# Start Flow Local in terminal mode (no menu bar). Run:  bash run.sh
# For the menu bar app, use make_app.sh instead.
cd "$(dirname "$0")"
source .venv/bin/activate
python flow_local.py
