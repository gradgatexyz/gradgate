#!/bin/sh
# the engine alone, without the gradgate command: http://127.0.0.1:8765 (reads ../.env)
cd "$(dirname "$0")" && set -a && [ -f ../.env ] && . ../.env; set +a
exec ../.venv/bin/uvicorn server:app --host 127.0.0.1 --port "${GRADGATE_PORT:-8765}" --timeout-graceful-shutdown 2
