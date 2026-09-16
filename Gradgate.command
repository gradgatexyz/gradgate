#!/bin/sh
# gradgate for macOS: double-click. The first run installs (a minute), every run starts the engine and opens the terminal.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/gradgate ]; then
  echo "installing gradgate (first run only)…"
  ./install.sh || { echo; echo "install failed — python 3.11+ is needed: https://www.python.org/downloads/"; read -r _; exit 1; }
fi
exec .venv/bin/gradgate start
