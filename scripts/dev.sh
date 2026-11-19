#!/usr/bin/env bash
# Start the API and the editor. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm install)
fi

export BANCWRIGHT_HOST="${BANCWRIGHT_HOST:-127.0.0.1}"
export BANCWRIGHT_PORT="${BANCWRIGHT_PORT:-47232}"

.venv/bin/python -m bancwright --host "$BANCWRIGHT_HOST" --port "$BANCWRIGHT_PORT" &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT

cd frontend
npm run dev -- --host "${BANCWRIGHT_HOST}" --port 47231 --strictPort
