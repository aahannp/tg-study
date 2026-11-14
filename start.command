#!/bin/bash
# Double-click launcher (macOS). Creates backend/.venv on first run, then opens the desktop window.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install it from https://www.python.org/downloads/ (or: brew install python) and run this again."
  read -r -p "Press Enter to close..."
  exit 1
fi
# (Re)install when the venv is missing or requirements.txt changed since the last install.
STAMP=backend/.venv/.requirements-installed
if [ ! -x backend/.venv/bin/python ] || ! cmp -s backend/requirements.txt "$STAMP"; then
  echo "Setting up dependencies (a minute or two on first run)..."
  { [ -x backend/.venv/bin/python ] || python3 -m venv backend/.venv; } &&
    backend/.venv/bin/pip install -q -r backend/requirements.txt && cp backend/requirements.txt "$STAMP" || {
    echo "Setup failed. See the error above."; read -r -p "Press Enter to close..."; exit 1; }
fi
exec backend/.venv/bin/python desktop.py
