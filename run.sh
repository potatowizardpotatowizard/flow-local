#!/bin/bash
# Start Flow Local. Run:  bash run.sh
cd "$(dirname "$0")"
source .venv/bin/activate
python flow_local.py
