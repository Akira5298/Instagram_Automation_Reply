#!/bin/bash
# ECHO — start the backend + Cloudflare Tunnel on your Mac. Ctrl-C stops both.
#
#   ./scripts/start.sh
#
# Tunnel mode is picked from backend/.env:
#   • ECHO_PUBLIC_BASE_URL empty  → quick tunnel (free, no account). Random
#     https://….trycloudflare.com address that CHANGES every start; it's
#     written to backend/.public_url and the server picks it up automatically.
#   • ECHO_PUBLIC_BASE_URL + CLOUDFLARE_TUNNEL_NAME → starts your named tunnel
#     (fixed hostname; recommended once Meta is configured).
#   • ECHO_PUBLIC_BASE_URL only → your named tunnel already runs as a service
#     (`sudo cloudflared service install`); nothing is started for it.
#
# While running, `caffeinate` stops the Mac from idle-sleeping (Meta disables
# webhooks after ~1 hour of failed deliveries; ECHO re-subscribes at startup).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
LOGS="$ROOT/logs"
PORT="${ECHO_PORT:-8000}"
HOST="${ECHO_BIND:-127.0.0.1}"   # localhost only; the tunnel is the public door
mkdir -p "$LOGS"

env_value() {  # read KEY from backend/.env (empty if unset)
  # `|| true`: a missing key must give "", not abort the script under `set -euo pipefail`
  { grep -E "^$1=" "$BACKEND/.env" 2>/dev/null || true; } | tail -1 | cut -d= -f2- \
    | sed -e 's/^ *//' -e 's/ *$//' -e 's/^"//' -e 's/"$//'
}

tunnel_help() {
  cat <<'EOF'

⚠️  Cloudflare's quick-tunnel service didn't answer (api.trycloudflare.com timed out).
    This is a network problem between your Mac and Cloudflare, not an ECHO bug.
    Things to try:
      1. Turn off any VPN, and iCloud Private Relay (System Settings → Apple ID → iCloud → Private Relay).
      2. Check you can reach Cloudflare:
           curl -sS -m 10 -o /dev/null -w "%{http_code}\n" https://api.trycloudflare.com/
           nslookup api.trycloudflare.com
      3. Some Wi-Fi networks block QUIC/UDP — retry with:  ECHO_TUNNEL_PROTOCOL=http2 ./scripts/start.sh
      4. Try another network (e.g. phone hotspot), or wait a few minutes — quick tunnels have no uptime guarantee.
      5. For something stable, set up a named tunnel (docs/META_SETUP.md §2b).
    The backend is still running locally, so the iPhone Simulator works meanwhile.
EOF
}

[ -x "$BACKEND/.venv/bin/python" ] || { echo "Run ./scripts/setup_mac.sh first."; exit 1; }
command -v cloudflared >/dev/null 2>&1 || { echo "cloudflared missing: brew install cloudflared"; exit 1; }
PY="$BACKEND/.venv/bin/python"

PIDS=""
STOPPED=0
cleanup() {
  [ "$STOPPED" = 1 ] && return
  STOPPED=1
  echo; echo "Stopping ECHO…"
  for pid in $PIDS; do kill "$pid" 2>/dev/null || true; done
  rm -f "$BACKEND/.public_url"
  wait 2>/dev/null || true
  echo "Stopped."
}
trap cleanup EXIT INT TERM

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use (another ECHO still running?). Stop it or set ECHO_PORT."
  exit 1
fi

echo "▶ Migrations"
(cd "$BACKEND" && "$PY" -m app.migrate)

echo "▶ Backend on http://$HOST:$PORT  (logs: logs/server.log)"
rm -f "$BACKEND/.public_url"
(cd "$BACKEND" && exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" \
    --proxy-headers --forwarded-allow-ips 127.0.0.1 >>"$LOGS/server.log" 2>&1) &
SERVER_PID=$!
PIDS="$PIDS $SERVER_PID"
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "Server failed to start — see logs/server.log:"; tail -20 "$LOGS/server.log"; exit 1; }
  sleep 1
done

caffeinate -ims -w "$SERVER_PID" &
PIDS="$PIDS $!"

PUBLIC_URL="$(env_value ECHO_PUBLIC_BASE_URL)"
TUNNEL_NAME="$(env_value CLOUDFLARE_TUNNEL_NAME)"
: >"$LOGS/tunnel.log"
if [ -n "$PUBLIC_URL" ] && [ -n "$TUNNEL_NAME" ]; then
  echo "▶ Named Cloudflare Tunnel \"$TUNNEL_NAME\" → $PUBLIC_URL  (logs: logs/tunnel.log)"
  cloudflared tunnel --no-autoupdate run "$TUNNEL_NAME" >>"$LOGS/tunnel.log" 2>&1 &
  PIDS="$PIDS $!"
elif [ -n "$PUBLIC_URL" ]; then
  echo "▶ Using $PUBLIC_URL (CLOUDFLARE_TUNNEL_NAME not set — assuming your tunnel runs as a service)"
else
  echo "▶ Quick Cloudflare Tunnel (random address, changes every start)  (logs: logs/tunnel.log)"
  PROTO="${ECHO_TUNNEL_PROTOCOL:-auto}"
  for attempt in 1 2 3; do
    cloudflared tunnel --no-autoupdate --protocol "$PROTO" --url "http://127.0.0.1:$PORT" >>"$LOGS/tunnel.log" 2>&1 &
    TUNNEL_PID=$!
    PIDS="$PIDS $TUNNEL_PID"
    PUBLIC_URL=""
    for _ in $(seq 1 40); do
      PUBLIC_URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGS/tunnel.log" | grep -v '^https://api\.' | head -1 || true)"
      [ -n "$PUBLIC_URL" ] && break
      kill -0 "$TUNNEL_PID" 2>/dev/null || break   # cloudflared gave up (e.g. "failed to request quick Tunnel")
      sleep 1
    done
    [ -n "$PUBLIC_URL" ] && break
    kill "$TUNNEL_PID" 2>/dev/null || true
    if grep -q "failed to request quick Tunnel" "$LOGS/tunnel.log"; then
      echo "  attempt $attempt/3: Cloudflare didn't respond$( [ "$attempt" -lt 3 ] && echo ", retrying in $((attempt * 5))s…")"
    else
      echo "  attempt $attempt/3: no tunnel address yet (see logs/tunnel.log)"
    fi
    if [ "$attempt" -lt 3 ]; then sleep $((attempt * 5)); fi
  done
  if [ -z "$PUBLIC_URL" ]; then
    tunnel_help
  else
    echo "$PUBLIC_URL" >"$BACKEND/.public_url"
  fi
fi

if [ -n "$PUBLIC_URL" ]; then
  printf "  waiting for %s to come online" "$PUBLIC_URL"
  for _ in $(seq 1 45); do
    curl -fsS "$PUBLIC_URL/health" >/dev/null 2>&1 && { echo " ✓"; break; }
    printf "."; sleep 2
  done
fi

echo
(cd "$BACKEND" && "$PY" -m app.doctor) || true

cat <<EOF
ECHO is running.
  • iPhone Simulator → Backend URL: http://localhost:$PORT
  • Physical iPhone  → Backend URL: ${PUBLIC_URL:-(no tunnel — see warning above)}
  • Logs: tail -f logs/server.log
Press Ctrl-C to stop.
EOF

wait "$SERVER_PID"
