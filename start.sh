#!/usr/bin/env bash
# Start the Claude Orchestrator (backend + frontend), no Docker.
# First run creates the backend venv and installs deps. Ctrl+C stops both.
#
# Ports are configurable:  CO_BACKEND_PORT (default 8200)  CO_FRONTEND_PORT (default 5200)
# Bind address: CO_BIND_HOST (default 127.0.0.1); proxy IPs: CO_FORWARDED_ALLOW_IPS

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# Read ONLY these keys from backend/.env (when not already set in the shell);
# the file is parsed, never sourced.
_env_key() {
  [ -f "$ROOT/backend/.env" ] || return 0
  grep -E "^[[:space:]]*$1=" "$ROOT/backend/.env" | tail -n1 \
    | sed -E "s/^[[:space:]]*$1=//; s/^[\"']//; s/[\"'][[:space:]]*$//; s/[[:space:]]+$//"
}
[ -z "${CO_BIND_HOST:-}" ] && CO_BIND_HOST="$(_env_key CO_BIND_HOST)"
[ -z "${CO_FORWARDED_ALLOW_IPS:-}" ] && CO_FORWARDED_ALLOW_IPS="$(_env_key CO_FORWARDED_ALLOW_IPS)"
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
# The WebSocket Origin allowlist is CO_CORS_ORIGINS; follow a custom frontend port
# unless it is set in the shell or in backend/.env (the backend reads .env itself).
if [ -z "${CO_CORS_ORIGINS:-}" ] && [ -z "$(_env_key CO_CORS_ORIGINS)" ]; then
  export CO_CORS_ORIGINS="[\"http://localhost:$FRONTEND_PORT\",\"http://127.0.0.1:$FRONTEND_PORT\"]"
fi
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
