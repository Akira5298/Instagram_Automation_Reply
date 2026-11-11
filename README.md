# ECHO — AI personal messaging assistant (iOS)

**Phase 4: real Instagram.** ECHO now connects to your Instagram Creator/Business account through Meta's official *Instagram API with Instagram Login*:

- **Login:** a secure browser sheet runs Business Login for Instagram. Tokens are encrypted on your server and never stored on the phone.
- **Receiving:** your DMs arrive by signed webhook, are stored once (deduplicated), and open each friend's 24-hour window.
- **Sending:** scheduled messages go out through the Graph API. ECHO paces sends and maps Instagram's errors to retry, fail, or "outcome unknown".
- **Echoes:** messages ECHO sent are recognised when Instagram echoes them back, so ECHO never reacts to itself. Replies you type in the Instagram app are recorded as yours.
- **Maintenance:** tokens refresh automatically, message text is purged after 30 days, and Meta's deauthorize and data-deletion callbacks plus a privacy page are built in.

New people who DM you always start with **AI replies off and consent "Not asked"**. Automatic replies to incoming messages arrive in Phase 5.

➡️ **Start with [`docs/META_SETUP.md`](docs/META_SETUP.md)**: Meta app, webhooks, testing, App Review and deploying to Railway.

## Upgrade from Phase 3

1. **Run the new migration.** Supabase SQL Editor, or:
   ```bash
   psql echo -f backend/supabase/migrations/0004_instagram.sql
   ```
2. **Update packages.** This adds `cryptography` and `python-multipart`.
   ```bash
   pip install -r backend/requirements.txt
   ```
3. **Get an https URL.** Meta requires https for both login and webhooks. Deploy to Railway (META_SETUP §6) or use a tunnel (`cloudflared tunnel --url http://localhost:8000`).
4. **Create the Meta app and fill in the `META_*` and `ECHO_*` settings.** Follow META_SETUP §1–4. Then set `ECHO_MESSAGING_PROVIDER=instagram`.
5. **Restart the server.** `/health` should report `"version": "0.4.0"`.
6. **Rebuild the app** (`cd ios && xcodegen generate`, then ⌘R). In the app:
   - Set **Settings → AI & backend → Backend URL** to the https URL.
   - Tap **Settings → Instagram → Connect Instagram**.

To keep testing without Instagram, leave `ECHO_MESSAGING_PROVIDER=mock`. Everything from Phase 3 still works.

## What happens to a DM now

```
Friend sends DM ─▶ Meta webhook (X-Hub-Signature-256 verified) ─▶ webhook_events (unique per mid) ─▶ 200 OK
                                                                        │ worker, a few seconds later
      ┌─────────────────────────────────────────────────────────────────┘
      ├─ is_self / reactions / seen      → ignored
      ├─ is_deleted                      → text removed
      ├─ is_echo of ECHO's own send      → ignored (loop guard: mid already stored)
      ├─ is_echo you typed in Instagram  → stored as "me"
      └─ inbound                         → friend found or created (name via User Profile API,
                                           AI off, consent unknown), message stored, 24h window opened
```

Scheduled sends still pass, at dispatch time, the safety gate and the deterministic eligibility check: 24-hour window, consent, per-friend toggle, emergency pause and account status. The send then goes to `POST graph.instagram.com/v26.0/<IG_ID>/messages`.

## Backend layout (new in Phase 4)

```
backend/app/
├── integrations/meta/instagram_client.py  # OAuth, token exchange/refresh, me, subscribe, send, profile, conversations
├── integrations/meta/signed_request.py    # Meta deauthorize / data-deletion signatures
├── core/crypto.py                         # AES-256-GCM token encryption
├── services/instagram_service.py          # connect, status, refresh, sync, friend creation
├── services/webhook_service.py            # ingest + process webhooks (worker)
├── messaging/instagram_provider.py        # real delivery, error mapping, pacing
├── api/instagram.py                       # /v1/instagram/* + /privacy
├── Procfile, .python-version              # Railway / Render
└── supabase/migrations/0004_instagram.sql
ios/Echo/Features/Settings/InstagramSettingsSection.swift   # ASWebAuthenticationSession login
```

## Tests

```bash
cd backend
ECHO_TEST_DATABASE_URL=postgresql://localhost/echo_test pytest -q   # 121 passed
```

Phase 4 tests run against a **fake Graph API** (`tests/fake_graph.py`). They cover:
- the full login: state, code exchange, long-lived token, account type and permission checks, encrypted storage, webhook subscription
- duplicate webhooks
- the echo loop guard
- deleted messages
- conversation sync
- token refresh
- Graph errors mapped to retry, fail, or expired login
- the deletion and deauthorize callbacks

iOS: ⌘U runs 27 tests.

Nothing here has run against the real Instagram API yet. That happens when you complete META_SETUP. If the real API behaves differently, send me the server log line (it includes Meta's error code and message) and I'll adjust.

## Earlier phases

- **Phase 1:** design system, five screens, schema.
- **Phase 2:** Claude chat and personality memory.
- **Phase 3:** scheduler worker, recurrence, safety and eligibility gates, activity feed.

The Phase 2 and 3 setup notes are in `docs/ARCHITECTURE.md` §3 and §8.
