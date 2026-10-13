#!/bin/bash
# Start voice-to-doc on http://127.0.0.1:8000 and open it in the browser.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
(sleep 2 && { open http://127.0.0.1:8000 2>/dev/null || xdg-open http://127.0.0.1:8000 2>/dev/null; } || true) &
exec .venv/bin/uvicorn --factory voice_to_doc.app:create_app --host 127.0.0.1 --port 8000
