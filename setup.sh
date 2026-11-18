#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -v
echo 'ALL PASSED'
