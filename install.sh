#!/bin/sh
# gradgate: a virtual environment, the gradgate command and a .env with the free public endpoints.
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "gradgate needs python 3.11 or newer ($("$PY" --version 2>&1))"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .
[ -f .env ] || cp .env.example .env
echo
echo "  gradgate_ is installed."
echo
echo "  .venv/bin/gradgate doctor   check the rpc, websocket and settings"
echo "  .venv/bin/gradgate start    the engine and the terminal on http://127.0.0.1:8765"
echo
