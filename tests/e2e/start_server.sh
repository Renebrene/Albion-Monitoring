#!/usr/bin/env bash
set -euo pipefail

ROOT="${E2E_ROOT:?E2E_ROOT is required}"
PORT="${E2E_PORT:?E2E_PORT is required}"
APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

mkdir -p "$ROOT/uploads"
export DB_PATH="$ROOT/e2e.db"
export UPLOADS_DIR="$ROOT/uploads"
export JWT_SECRET="e2e-only-secret-0123456789abcdefghij"
unset PRIVATE_MEDIA_DIR

cd "$APP_DIR"
exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$PORT" --workers 1
