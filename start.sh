#!/bin/sh
# Local run without Docker: http://127.0.0.1:8765
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  PY=$(command -v python3.12 || command -v python3.13 || command -v python3.11) || true
  [ -z "$PY" ] && { echo "Python >= 3.11 is required (brew install python@3.12)"; exit 1; }
  "$PY" -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
if [ ! -f web/build/index.html ]; then
  (cd web && npm ci && npm run build)
fi
exec .venv/bin/uvicorn crossfade.api.app:app --host 127.0.0.1 --port 8765
