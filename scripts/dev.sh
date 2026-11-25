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

if [[ ! -x .venv/bin/maturin ]]; then
  .venv/bin/pip install "maturin>=1.7,<2"
fi
.venv/bin/maturin develop --release --manifest-path native/Cargo.toml
.venv/bin/python -c 'from ocarina_forge.extension import module; import sys; native = module(); print("Rust extension:", getattr(native, "__file__", None)); sys.exit(native is None)'

# BANCWRIGHT_HOST and BANCWRIGHT_PORT still work for this release.
export OCARINA_FORGE_HOST="${OCARINA_FORGE_HOST:-${BANCWRIGHT_HOST:-127.0.0.1}}"
export OCARINA_FORGE_PORT="${OCARINA_FORGE_PORT:-${BANCWRIGHT_PORT:-47232}}"

.venv/bin/python -m ocarina_forge --host "$OCARINA_FORGE_HOST" --port "$OCARINA_FORGE_PORT" &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT

cd frontend
npm run dev -- --host "${OCARINA_FORGE_HOST}" --port 47231 --strictPort
