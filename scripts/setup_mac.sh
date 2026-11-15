#!/bin/bash
# ECHO — one-time (and safe to re-run) setup for running locally on macOS.
#
#   cd ~/Downloads/Instagram_App && ./scripts/setup_mac.sh
#
# Uses what you already have where possible:
#   • Python 3.12 (installs python@3.12 with Homebrew only if missing)
#   • PostgreSQL on localhost:5432 (installs/starts postgresql@16 only if none is running)
#   • cloudflared (Cloudflare Tunnel client)
# Then: creates database "echo" if missing, a virtualenv in backend/.venv,
# installs Python packages, fills in missing secrets in backend/.env (never
# overwrites values you've set), and applies database migrations.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
DB_NAME="${ECHO_DB_NAME:-echo}"

say()  { printf "\n\033[1;35m▶ %s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }

say "Checking Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh — install it, then re-run this script."
  exit 1
fi
ok "$(brew --version | head -1)"

say "Python 3.12"
if command -v python3.12 >/dev/null 2>&1; then
  PY="$(command -v python3.12)"
else
  brew list --versions python@3.12 >/dev/null 2>&1 || brew install python@3.12
  PY="$(brew --prefix python@3.12)/bin/python3.12"
fi
ok "$("$PY" --version) at $PY"

say "PostgreSQL"
find_pg_tool() {
  if command -v "$1" >/dev/null 2>&1; then command -v "$1"; return; fi
  for prefix in "$(brew --prefix postgresql@16 2>/dev/null)" "$(brew --prefix postgresql@17 2>/dev/null)" \
                "/Applications/Postgres.app/Contents/Versions/latest"; do
    if [ -n "$prefix" ] && [ -x "$prefix/bin/$1" ]; then echo "$prefix/bin/$1"; return; fi
  done
}
PG_ISREADY="$(find_pg_tool pg_isready || true)"
if [ -n "$PG_ISREADY" ] && "$PG_ISREADY" -q -h localhost -p 5432; then
  ok "using the PostgreSQL already running on localhost:5432"
else
  warn "no PostgreSQL running on localhost:5432 — installing/starting postgresql@16"
  brew list --versions postgresql@16 >/dev/null 2>&1 || brew install postgresql@16
  brew services start postgresql@16 >/dev/null
  PG_ISREADY="$(brew --prefix postgresql@16)/bin/pg_isready"
  for _ in $(seq 1 30); do "$PG_ISREADY" -q -h localhost -p 5432 && break; sleep 1; done
  "$PG_ISREADY" -q -h localhost -p 5432 || { echo "PostgreSQL didn't start. Check: brew services list"; exit 1; }
  ok "postgresql@16 running"
fi
PSQL="$(find_pg_tool psql)"
CREATEDB="$(find_pg_tool createdb)"
if "$PSQL" -h localhost -d postgres -Atc "select 1 from pg_database where datname = '$DB_NAME'" | grep -q 1; then
  ok "database \"$DB_NAME\" exists (your data is kept)"
else
  "$CREATEDB" -h localhost "$DB_NAME"
  ok "created database \"$DB_NAME\""
fi

say "cloudflared"
command -v cloudflared >/dev/null 2>&1 || brew install cloudflared
ok "$(cloudflared --version 2>&1 | head -1)"

say "Python environment (backend/.venv)"
if [ ! -x "$BACKEND/.venv/bin/python" ]; then
  "$PY" -m venv "$BACKEND/.venv"
fi
"$BACKEND/.venv/bin/python" -m pip install -q --upgrade pip
"$BACKEND/.venv/bin/python" -m pip install -q -r "$BACKEND/requirements.txt"
ok "packages installed"

say "Configuration (backend/.env)"
if [ ! -f "$BACKEND/.env" ]; then
  cp "$BACKEND/.env.example" "$BACKEND/.env"
  ok "created backend/.env from .env.example"
fi
"$BACKEND/.venv/bin/python" - "$BACKEND/.env" "$DB_NAME" <<'PY'
import base64, os, re, secrets, sys
path, db = sys.argv[1], sys.argv[2]
text = open(path).read()
defaults = {
    "ECHO_API_TOKEN": secrets.token_urlsafe(32),
    "ECHO_TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "META_WEBHOOK_VERIFY_TOKEN": secrets.token_urlsafe(24),
    "DATABASE_URL": f"postgresql://localhost:5432/{db}",
}
placeholders = {"", "change-me", "dev-token"}
for key, value in defaults.items():
    m = re.search(rf"^{key}=(.*)$", text, flags=re.M)
    if m is None:
        text += f"\n{key}={value}\n"
        print(f"  ✓ added {key}")
    elif m.group(1).strip() in placeholders:
        text = text[:m.start()] + f"{key}={value}" + text[m.end():]
        print(f"  ✓ generated {key}")
    else:
        print(f"  ✓ kept your {key}")
# Keys that may legitimately stay empty (empty = quick tunnel); add them so they're easy to find.
optional = {
    "ECHO_PUBLIC_BASE_URL": "empty → quick tunnel",
    "CLOUDFLARE_TUNNEL_NAME": "empty → quick tunnel",
    "META_LOGIN_MODE": "unified",
    "META_APP_ID": "fill in from App settings → Basic",
    "META_LOGIN_CONFIG_ID": "fill in from Instagram → API setup with Unified login",
}
for key, note in optional.items():
    if re.search(rf"^{key}=", text, flags=re.M) is None:
        value = "unified" if key == "META_LOGIN_MODE" else ""
        text += f"\n{key}={value}\n"
        print(f"  ✓ added {key}={value} ({note})")
open(path, "w").write(text)
PY
chmod 600 "$BACKEND/.env"

say "Database migrations"
(cd "$BACKEND" && .venv/bin/python -m app.migrate)

say "Done"
cat <<EOF
Next:
  1. Edit backend/.env: ANTHROPIC_API_KEY, ECHO_AI_PROVIDER=anthropic, and the META_* values
     (docs/META_SETUP.md walks you through the Meta side).
  2. Start everything:   ./scripts/start.sh
  3. Your app token (Settings → AI & backend → API token):
       grep ECHO_API_TOKEN backend/.env
EOF
