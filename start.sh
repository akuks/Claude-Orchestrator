#!/usr/bin/env bash
# Start the Claude Orchestrator (backend + frontend), no Docker.
# First run creates the backend venv and installs deps. Ctrl+C stops both.
#
# Ports are configurable:  CO_BACKEND_PORT (default 8200)  CO_FRONTEND_PORT (default 5200)

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${CO_BACKEND_PORT:-8200}"
FRONTEND_PORT="${CO_FRONTEND_PORT:-5200}"

# --- backend deps ---
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  echo "Creating backend virtualenv and installing deps (first run)…"
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

# --- frontend deps ---
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  echo "Installing frontend deps (first run)…"
  npm install
fi

# --- free the ports if something is already bound ---
lsof -ti "tcp:$BACKEND_PORT" 2>/dev/null | xargs kill 2>/dev/null || true
lsof -ti "tcp:$FRONTEND_PORT" 2>/dev/null | xargs kill 2>/dev/null || true

# --- launch ---
cd "$ROOT/backend"
./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" &
BACKEND_PID=$!

cd "$ROOT/frontend"
VITE_PROXY_TARGET="http://localhost:$BACKEND_PORT" VITE_PORT="$FRONTEND_PORT" npm run dev &
FRONTEND_PID=$!

cleanup() {
  echo ""
  echo "Stopping…"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM

echo ""
echo "  ▶ Backend:  http://localhost:$BACKEND_PORT   (API docs: /docs)"
echo "  ▶ Frontend: http://localhost:$FRONTEND_PORT"
echo "  Press Ctrl+C to stop both."
echo ""
wait
