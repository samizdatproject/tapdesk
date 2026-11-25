#!/usr/bin/env bash
# Starts TapDesk locally: whisper.cpp server (optional), backend (:8788) and plugin dev server (:5174).
# Ctrl-C stops everything this script started.
#
#   TAPDESK_ENV=path/to/tapdesk.env   env file (default: ~/.config/tapdesk/tapdesk.env), e.g.
#                                     TAPDESK_TON_ADDRESS=UQ...   (the wallet to watch, read-only)
#   WHISPER_SERVER=path/to/whisper-server   WHISPER_MODEL=path/to/ggml-base.en.bin   (both optional)
#
# No API keys are needed: all market and chain data is public.
set -euo pipefail
cd "$(dirname "$0")/.."
export TAPDESK_ENV="${TAPDESK_ENV:-$HOME/.config/tapdesk/tapdesk.env}"

if [[ ! -f "$TAPDESK_ENV" ]]; then
  echo "No env file at $TAPDESK_ENV: starting without a TON address (set one on the phone screen)."
  echo "To set it here instead: mkdir -p ~/.config/tapdesk && echo 'TAPDESK_TON_ADDRESS=UQ...' > $TAPDESK_ENV"
fi

pids=()
cleanup() { for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if [[ ! -d backend/.venv ]]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
if [[ ! -d plugin/node_modules ]]; then
  (cd plugin && npm ci --silent)
fi

if [[ -n "${WHISPER_SERVER:-}" && -n "${WHISPER_MODEL:-}" ]]; then
  "$WHISPER_SERVER" -m "$WHISPER_MODEL" --host 127.0.0.1 --port 8080 -nt >/dev/null 2>&1 &
  pids+=($!)
  echo "whisper-server on http://127.0.0.1:8080"
else
  echo "Voice commands off: set WHISPER_SERVER and WHISPER_MODEL to enable them."
fi

(cd plugin && npm run dev -- --clearScreen false >/dev/null 2>&1) &
pids+=($!)
echo "plugin on http://localhost:5174 (point the Even Hub simulator or the Even app at it)"

cd backend
PYTHONUNBUFFERED=1 .venv/bin/python -m tapdesk serve
