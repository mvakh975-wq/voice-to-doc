#!/bin/bash
# Start voice-to-doc on http://127.0.0.1:8000 and open it in the browser.
set -euo pipefail
cd "$(dirname "$0")"

PORT=8000
URL="http://127.0.0.1:$PORT"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
  echo "Нужен Python 3.12 или новее (python3 --version: $(python3 --version 2>&1))." >&2
  exit 1
fi

# Reinstall when dependencies change or a previous install was interrupted.
stamp=".venv/.installed"
want=$(cat requirements.txt constraints.txt | shasum | cut -d' ' -f1)
if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$want" ]; then
  echo "Устанавливаю зависимости…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt -c constraints.txt
  echo "$want" > "$stamp"
fi

if ! .venv/bin/python -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', $PORT))" 2>/dev/null; then
  echo "Порт $PORT занят — возможно, voice-to-doc уже запущен: $URL" >&2
  exit 1
fi

(sleep 2 && { open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null; } || true) &
exec .venv/bin/uvicorn --factory voice_to_doc.app:create_app --host 127.0.0.1 --port "$PORT"
