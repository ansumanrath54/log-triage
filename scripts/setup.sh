#!/usr/bin/env bash
# One-time setup for macOS / Linux. Run from the project root:  bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Creating Python virtual environment (.venv)"
python3 -m venv .venv

echo "==> Installing backend dependencies"
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r backend/requirements.txt

echo "==> Installing frontend packages"
(cd frontend && npm install)

echo
echo "Done. Next: open this folder in VS Code and follow the 'Open in VS Code' section of README.md"
