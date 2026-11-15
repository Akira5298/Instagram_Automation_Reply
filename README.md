# ECHO — AI personal messaging assistant (iOS)

ECHO runs entirely on your Mac: **FastAPI (Python 3.12) + local PostgreSQL + Claude API + a tunnel** (Cloudflare, or ngrok's free fixed domain via `ECHO_PUBLIC_BASE_URL`) for the public https address Meta needs. There are no hosting costs; you pay only for Claude API usage.

**Phase 5 — automatic replies.** When a friend who agreed DMs you, ECHO answers in your voice:
- **Gates, in order:** consent, AI on, you haven't taken over, global pause, daily limits, safety check, then Meta's 24-hour window. All of them are deterministic code; Claude only writes the text.
- **Waits a moment first** (per friend, default 45 s), so a burst of messages gets one reply.
- **Steps back when you reply yourself**, from ECHO or the Instagram app, for 30 minutes (configurable). You can also "Take over" a chat explicitly.
- **Holds drafts for you** when a message involves money, plans, commitments, something sensitive or emotional, a photo it can't see, or anything only you can answer. They go to the **Needs you** queue, where you edit, send or dismiss them. You can also set a friend to "check every reply with me".
- **Consent by DM:** "Ask by DM" sends your consent request; a YES reply grants consent and STOP revokes it, recorded automatically with the message as evidence.
- **Per-friend reply style:** tone, formality, emoji, length, topics to avoid, custom instructions.
- **Disclosure:** the AI disclosure message goes out before the first automatic reply, and again after 30 days. Missed messages from while the Mac was off (over 2 hours old) are never answered late.

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

1. Copy the new files over your existing project, **keeping your `backend/.env`** and `backend/.venv`. Then run `cd ios && xcodegen generate` so Xcode picks up the new Swift files.
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
│   │   ├── services/                 # chat, scheduler, instagram, webhooks, auto_reply (inbound/consent/takeover), delivery
│   │   ├── worker/runner.py          # scheduler + webhook processing + hourly maintenance
│   │   ├── worker/auto_reply.py      # Phase 5: automatic-reply pipeline (gates → Claude → send)
│   │   └── api/                      # /v1/* , /webhooks/meta , /privacy
│   ├── migrations/0001…0007.sql      # plain PostgreSQL
│   ├── tests/                        # 148 tests (fake Graph API + real PostgreSQL)
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

The suite has 148 tests, including the automatic-reply pipeline end to end, the migration runner and tunnel URL resolution. iOS: ⌘U.
