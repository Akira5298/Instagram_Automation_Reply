# ECHO — AI personal messaging assistant (iOS)

ECHO runs entirely on your Mac: **FastAPI (Python 3.12) + local PostgreSQL + Claude API + a Cloudflare Tunnel** for the public https address Meta needs. There are no hosting costs; you pay only for Claude API usage.

**Phase 4 — real Instagram.**
- Meta's Instagram API with Unified Login (sign in with Instagram or Facebook, no Facebook Page needed), with encrypted tokens.
- Signed webhooks bring in your DMs.
- Scheduled messages are delivered through the official Graph API.
- Meta's deauthorize and data-deletion callbacks are built in, plus a privacy page.

New people who DM you always start with AI replies **off** and consent **"Not asked"**.

➡️ **Full guide: [`docs/META_SETUP.md`](docs/META_SETUP.md)**: Mac setup, Cloudflare Tunnel, Meta app, testing, App Review, and laptop caveats.

## Quick start

```bash
cd ~/Downloads/Instagram_App
chmod +x scripts/*.sh
./scripts/setup_mac.sh     # once: Python env, PostgreSQL "echo", cloudflared, secrets, migrations
# edit backend/.env → ECHO_AI_PROVIDER=anthropic, ANTHROPIC_API_KEY=…, and later META_*
./scripts/start.sh         # backend + Cloudflare Tunnel + caffeinate; prints your https URL and checks
```

In a second terminal, while ECHO runs:

```bash
cd ~/Downloads/Instagram_App/backend
.venv/bin/python -m app.doctor           # re-run the pre-flight checks any time
.venv/bin/python -m app.migrate --status # which database migrations are applied
tail -f ../logs/server.log
```

iOS: `cd ios && xcodegen generate && open Echo.xcodeproj`, then ⌘R. In the app, go to Settings → AI & backend and set:
- **Backend URL:** `http://localhost:8000` in the Simulator, or the tunnel's `https://…` address on an iPhone
- **API token:** the value of `ECHO_API_TOKEN` from `backend/.env`

## Updating your existing project

1. Copy the new files over your existing project, **keeping your `backend/.env`** and `backend/.venv`.
2. Delete the old `backend/supabase/` folder. Migrations now live in `backend/migrations/`.
3. Delete `backend/Procfile`. It was only used for Railway.
4. Run `./scripts/setup_mac.sh`. It adds new packages and settings, and records the migrations you already applied by hand.

## What's in the box

```
Instagram_App/
├── scripts/
│   ├── setup_mac.sh                  # one-time setup (idempotent)
│   ├── start.sh                      # run backend + tunnel (quick or named) + caffeinate
│   └── cloudflared/config.example.yml# named-tunnel config template
├── backend/
│   ├── app/
│   │   ├── migrate.py                # python -m app.migrate  (tracks + detects applied migrations)
│   │   ├── doctor.py                 # python -m app.doctor   (pre-flight + Meta dashboard URLs)
│   │   ├── core/public_url.py        # tunnel URL: ECHO_PUBLIC_BASE_URL or backend/.public_url
│   │   ├── integrations/meta/        # Instagram Graph API client, webhook signatures, signed_request
│   │   ├── services/                 # chat, scheduler, instagram (connect/refresh/re-subscribe), webhooks
│   │   ├── worker/runner.py          # scheduler + webhook processing + hourly maintenance
│   │   └── api/                      # /v1/* , /webhooks/meta , /privacy
│   ├── migrations/0001…0005.sql      # plain PostgreSQL
│   ├── tests/                        # 124 tests (fake Graph API + real PostgreSQL)
│   └── .env.example
├── ios/                              # SwiftUI app (XcodeGen project.yml)
└── docs/ARCHITECTURE.md, docs/META_SETUP.md
```

## Laptop-specific behaviour

- **Sleep:** the server binds to `127.0.0.1` only, and the tunnel is the single way in. `start.sh` keeps the Mac awake with `caffeinate` while ECHO runs, but closing the lid still sleeps it.
- **Missed webhooks:** Meta unsubscribes webhooks after about an hour of failed deliveries. ECHO re-subscribes at every start and every hour. Use **Settings → Instagram → Sync recent conversations** to backfill.
- **Scheduled messages missed while offline** are **blocked, never sent late**, and you get a notification.
- **Quick-tunnel addresses change on every start.** Update the Meta dashboard and the iPhone app each time, or set up a named tunnel (META_SETUP §2b).

## Tests

```bash
cd backend
createdb echo_test   # disposable; tests wipe it
ECHO_TEST_DATABASE_URL=postgresql://localhost/echo_test .venv/bin/python -m pytest -q
```

The suite has 124 tests, including the migration runner and tunnel URL resolution. iOS: ⌘U.
