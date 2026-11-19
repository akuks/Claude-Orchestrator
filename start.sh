#!/usr/bin/env bash
# Start the Claude Orchestrator (backend + frontend), no Docker.
# First run creates the backend venv and installs deps. Ctrl+C stops both.
#
# Ports are configurable:  CO_BACKEND_PORT (default 8200)  CO_FRONTEND_PORT (default 5200)
# Bind address: CO_BIND_HOST (default 127.0.0.1); proxy IPs: CO_FORWARDED_ALLOW_IPS

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
# Binds localhost by default. Behind a reverse proxy, set CO_FORWARDED_ALLOW_IPS to the
# proxy's IP so the login lockout sees real client IPs. Single process: no --workers.
./.venv/bin/uvicorn app.main:app --host "${CO_BIND_HOST:-127.0.0.1}" --port "$BACKEND_PORT" \
  --proxy-headers --forwarded-allow-ips "${CO_FORWARDED_ALLOW_IPS:-127.0.0.1}" &
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
