# ECHO — AI personal messaging assistant (iOS)

ECHO runs entirely on your Mac: **FastAPI (Python 3.12) + local PostgreSQL + Claude API + a tunnel** (Cloudflare, or ngrok's free fixed domain via `ECHO_PUBLIC_BASE_URL`) for the public https address Meta needs. There are no hosting costs; you pay only for Claude API usage.

**Phase 7 — smarter replies.**
- **Tap-to-send suggestions in Needs you:** every held reply comes with 2–3 options in your voice (yes / no / maybe-style), plus ❤️ React. Tap one to send it, long-press to edit, or write your own. **More options** asks for fresh ones.
- **Reply mode** (Settings → Automatic replies): *Fully automatic*, or *Review every reply* (everything waits in Needs you with suggestions).
- **Reply length:** Short, Medium, Long or Adaptive (mirrors the friend and how you usually write to them).
- **Learns from your chats:** after every few messages you type yourself, ECHO refines how you write to *that* person (tone, slang, nicknames, emoji, humour, length) and a few general habits for your profile. See and reset it in each friend's **Reply style**. ECHO never learns from its own replies. Turn off with **Learn from my chats**.
- **Shared posts, Reels and photos:** ECHO reads the caption and looks at photos/post previews, then answers like you would: a short comment, emoji, or a ❤️ reaction. It can't watch videos, so it keeps those replies general. Voice notes still come to you.
- **Message ideas in the Scheduler:** ✨ Suggest gives ideas that fit the relationship (jokes for close friends, sweet ones for a partner, check-ins for family), optionally for an occasion. Pick one and it's sent **word for word**. New presets: **Partner** and **Family**.
- **Your day (optional):** share your **city** (never GPS coordinates) and **calendar busy times** for the next 36 h, so replies know when you're busy. ECHO never volunteers where you are or what you're doing; a draft that names your city or an event waits for your OK.
- **Push is off in this build** (free Apple account). Everything else, including live updates, works. To turn push on later, see [`docs/TESTFLIGHT.md`](docs/TESTFLIGHT.md) §3.

**Phase 6 — ready to live with.**
- **Push notifications** via APNs from your Mac, including "Send test". Tapping one opens the chat or the **Needs you** queue. Setup: [`docs/TESTFLIGHT.md`](docs/TESTFLIGHT.md) §3.
- **Quiet hours:** no pushes overnight, and optionally no automatic replies either. Notification preferences now live on your server.
- **Friend memories:** **What ECHO remembers about…** in each chat. You add, pause, approve or forget them. Sensitive ones expire after at most 90 days, and only approved ones are used.
- **Security review** ([`docs/SECURITY.md`](docs/SECURITY.md)):
  - secrets are masked in all logs
  - weak tokens are refused over the tunnel
  - repeated token guessing is throttled
  - API docs are local-only
  - security headers and a body-size limit
  - a prompt-injection backstop
- **TestFlight-ready:** app icon, privacy manifest, export-compliance key, push entitlement, and a step-by-step guide.

**Phase 5 — automatic replies.** When a friend who agreed DMs you, ECHO answers in your voice:
- **Gates, in order:** consent, AI on, you haven't taken over, global pause, daily limits, safety check, then Meta's 24-hour window. All of them are deterministic code; Claude only writes the text.
- **Waits a moment first** (per friend, default 45 s), so a burst of messages gets one reply.
- **Steps back when you reply yourself**, from ECHO or the Instagram app, for 30 minutes (configurable). You can also "Take over" a chat explicitly.
- **Holds drafts for you** when a message involves money, plans, commitments, something sensitive or emotional, or anything only you can answer. They go to the **Needs you** queue, where you edit, send or dismiss them. You can also set a friend to "check every reply with me".
- **Consent by DM:** "Ask by DM" sends your consent request; a YES reply grants consent and STOP revokes it, recorded automatically with the message as evidence.
- **Per-friend reply style:** tone, formality, emoji, length, topics to avoid, custom instructions.
- **Live:** the app keeps one long-poll request open (`/v1/updates`, about 2 requests a minute), so new DMs, ECHO's replies and held drafts appear within a second or two, with no manual refresh.
- **Safety net for missed webhooks:** every 30 s (`INSTAGRAM_POLL_SECONDS`) the server pulls your 5 latest chats. Anything new there is treated like a webhook, so unanswered messages still get replies.
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
│   │   ├── worker/auto_reply.py      # automatic-reply pipeline (gates → Claude → send / react / hold with suggestions)
│   │   ├── services/style_learning.py# learns how you write to each friend (hourly)
│   │   ├── services/media.py         # shared posts/Reels/photos → text + safe image fetch
│   │   └── api/                      # /v1/* , /webhooks/meta , /privacy
│   ├── migrations/0001…0009.sql      # plain PostgreSQL
│   ├── tests/                        # 179 tests (fake Graph API + real PostgreSQL)
│   └── .env.example
├── ios/                              # SwiftUI app (XcodeGen project.yml)
└── docs/ARCHITECTURE.md, META_SETUP.md, TESTFLIGHT.md, SECURITY.md
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

The suite has 179 tests, including the automatic-reply pipeline end to end, the migration runner and tunnel URL resolution. iOS: ⌘U.
