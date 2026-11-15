# ECHO on your Mac — local setup + connecting Instagram

*Primary environment: your MacBook Air. Python 3.12 + FastAPI, local PostgreSQL database `echo`, Anthropic Claude API, a Cloudflare Tunnel for the public https address, and Meta's official Instagram API. No Railway, no Supabase, no hosting bill. Meta and Cloudflare details checked November 2026; Meta's dashboard menus change often.*

Project folder used below: `~/Downloads/Instagram_App` (contains `backend/`, `ios/`, `docs/`, `scripts/`).

---

## 1. Set up the Mac (once)

```bash
cd ~/Downloads/Instagram_App
chmod +x scripts/*.sh
./scripts/setup_mac.sh
```

The script is safe to re-run. It keeps your existing PostgreSQL, database and `.env` values, and does the following:

- Uses Python 3.12. Homebrew installs it only if it's missing.
- Uses the PostgreSQL already running on `localhost:5432`. Only if none is running does it install and start `postgresql@16`.
- Creates database **`echo`** if it doesn't exist. Your Phase 2–3 data stays.
- Installs `cloudflared`.
- Creates `backend/.venv` and installs the Python packages.
- Fills in *missing* secrets in `backend/.env`: `ECHO_API_TOKEN`, `ECHO_TOKEN_ENCRYPTION_KEY`, `META_WEBHOOK_VERIFY_TOKEN` and `DATABASE_URL=postgresql://localhost:5432/echo`. Values you already set are never overwritten.
- Applies database migrations with `python -m app.migrate`. Migrations you ran earlier by hand with `psql` are recognised and recorded, not re-run.

Then edit `backend/.env`:

```bash
ECHO_AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-…
```

If macOS asks whether Terminal may access your Downloads folder, choose **Allow**.

## 2. The public https address (Cloudflare Tunnel)

Meta only talks to https URLs, for both login redirects and webhooks. A Cloudflare Tunnel gives your Mac one without opening ports on your router. Your server listens on `127.0.0.1` only; the tunnel is the only way in from outside.

### 2a. Quick tunnel (free, no account): good for getting started

Leave `ECHO_PUBLIC_BASE_URL` and `CLOUDFLARE_TUNNEL_NAME` empty in `.env`, then run:

```bash
./scripts/start.sh
```

This starts the backend, then `cloudflared tunnel --url http://127.0.0.1:8000`. That prints a random `https://<words>.trycloudflare.com` address. `start.sh` saves it to `backend/.public_url`, and the server reads it automatically.

At the end it runs **`python -m app.doctor`**. Doctor checks your settings, the database, your Claude key, that the tunnel reaches your server and that Meta's webhook verification will pass. It also **prints the exact URLs to paste into Meta**.

Quick tunnel limitations:
- **The address changes every time you restart `start.sh`.** Each time, you must update the Meta dashboard URLs (§4–5) and the Backend URL in the iPhone app. Doctor prints the new values.
- Cloudflare says quick tunnels are for testing: no uptime guarantee, a cap on concurrent requests (HTTP 429 beyond it), and no Server-Sent Events. ECHO's traffic is far below these limits.

### 2b. Named tunnel (fixed hostname): recommended once Meta is set up

A free Cloudflare account works, but you need **a domain managed by Cloudflare**. You can register a cheap one through Cloudflare. In return, the address never changes, so you configure Meta once.

```bash
cloudflared tunnel login                                  # opens the browser; pick your domain
cloudflared tunnel create echo                            # creates ~/.cloudflared/<UUID>.json
cloudflared tunnel route dns echo echo.yourdomain.com     # DNS record → this tunnel
cp scripts/cloudflared/config.example.yml ~/.cloudflared/config.yml
# edit ~/.cloudflared/config.yml: your Mac username, the <UUID>, your hostname
cloudflared tunnel ingress validate
```

In `backend/.env`:

```bash
ECHO_PUBLIC_BASE_URL=https://echo.yourdomain.com
CLOUDFLARE_TUNNEL_NAME=echo
```

`./scripts/start.sh` now runs the named tunnel instead of a quick one.

To have the tunnel start automatically at login instead, run `sudo cloudflared service install` and leave `CLOUDFLARE_TUNNEL_NAME` empty. `start.sh` then assumes the tunnel is already running.

## 3. Create the Meta app (Instagram API with Unified Login)

Meta apps created since autumn 2026 only offer **Instagram API with Unified Login**. The dashboard says that API setup with Instagram login isn't available and the app uses the integrated (Unified) login instead. ECHO uses Unified Login by default (`META_LOGIN_MODE=unified`).

Unified Login means:
- People sign in with **Instagram or Facebook** credentials, then pick the Instagram professional account to share.
- **No Facebook Page is needed.**
- The app must belong to a **business portfolio**.
- The login produces a **Business Integration System User token**. By default it never expires, so there's nothing to refresh.
- All calls go to `graph.facebook.com`.

Steps:

1. Turn your Instagram account into a **Creator** or **Business** account: Instagram → Settings → Account type and tools → Switch to professional account. Meta's API doesn't support personal accounts.
2. In the Instagram app, go to Settings → Messages and story replies → Message controls → **Connected tools** and turn **Allow access to messages** ON. Without it, no message webhooks arrive.
3. At developers.facebook.com, go to **My Apps → Create app**.
   - Use case: **"Manage messaging & content on Instagram"**.
   - **Connect a business portfolio.** Unified Login only appears for apps in a business portfolio. A portfolio for yourself is fine.
4. Open **App settings → Basic** and copy two values into `backend/.env`:
   ```bash
   META_LOGIN_MODE=unified
   META_APP_ID=<App ID>
   META_APP_SECRET=<App secret>
   ECHO_MESSAGING_PROVIDER=instagram
   ```
   These are the **Meta** app's ID and secret. Unified Login has no separate Instagram app ID.
5. Restart `./scripts/start.sh`. Doctor's *Instagram settings* line calls Meta with these two values and shows ✅ with your app's name when they belong together.

## 4. The Unified Login configuration

1. Go to **Use cases → Instagram → API setup with Unified login** (or **Instagram → API setup with Unified login** in the left menu).
2. **Create a login configuration**, which Meta stores as a *Facebook Login for Business* configuration:
   - **Token type:** *System-user access token*, expiry **Never**. ECHO also accepts a user-access-token configuration. It swaps that for a ~60-day token, and you reconnect when it runs out.
   - **Assets:** *Instagram accounts*.
   - **Permissions:** at least **`instagram_basic`** and **`instagram_manage_messages`**. The editor pre-ticks more (ads, publishing, comments); ECHO doesn't need them.
3. Copy the configuration's **ID** into `backend/.env`:
   ```bash
   META_LOGIN_CONFIG_ID=<configuration ID>
   ```
   If the dashboard's generated login URL contains extra parameters you want kept, such as `display=popup`, list them as `META_LOGIN_EXTRA_PARAMS=display=popup`. ECHO sets `client_id`, `config_id`, `redirect_uri`, `response_type` and `state` itself.
4. Add the URLs doctor printed. **Menu names vary between dashboard versions**; these live under **Facebook Login for Business → Settings** and **App settings → Basic**:

| Field | Value |
|---|---|
| Valid OAuth Redirect URIs | `https://<your-tunnel>/v1/instagram/oauth/callback` |
| Deauthorize callback URL | `https://<your-tunnel>/v1/instagram/deauthorize` |
| Data deletion request URL | `https://<your-tunnel>/v1/instagram/data-deletion` |
| Privacy policy URL | `https://<your-tunnel>/privacy` |

The redirect URI must match exactly. Each login remembers the address it started with, so if a quick-tunnel restart happens halfway through a login, that login fails and you start again.

## 5. Webhooks

1. In **API setup with Unified login**, open the webhooks step. If it isn't there, use **Webhooks** with the **Instagram** object. Set:
   - **Callback URL:** `https://<your-tunnel>/webhooks/meta`
   - **Verify token:** the value of `META_WEBHOOK_VERIFY_TOKEN` (`grep META_WEBHOOK_VERIFY_TOKEN backend/.env`)
2. Click **Verify and save**. Doctor's "Webhook verification ✅" means this will pass.
3. Subscribe to the **`messages`** field.

Webhooks are signed with the **Meta app secret** (`META_APP_SECRET`), which ECHO checks on every delivery.

Connecting Instagram from the app also subscribes your account: `POST graph.facebook.com/{IG_ID}/subscribed_apps?subscribed_fields=messages`. The server repeats this at every start and every hour. That matters on a laptop: **Meta unsubscribes an account when webhook deliveries keep failing for about an hour**, for example while your Mac sleeps.

## 6. Connect from the app

1. Run the app from Xcode (`cd ios && xcodegen generate`, then ⌘R).
2. **Settings → AI & backend:**
   - **Backend URL:**
     - Simulator: `http://localhost:8000`
     - iPhone: the `https://…` tunnel address
   - Paste `ECHO_API_TOKEN`, then tap **Test connection**.
3. **Settings → Instagram → Connect Instagram.** Meta's login opens in a secure browser sheet.
   - Sign in with Instagram or Facebook.
   - **Select your Instagram professional account** and allow the permissions.

   You return to the app showing **@yourname**, with Webhooks: **Subscribed**.
4. New DMs appear on their own. Webhooks deliver them instantly. As a backup, the server checks your **5 latest chats every 30 seconds**, and **Sync 5 latest chats now** does the same on demand. Both use `/{IG_ID}/conversations`, which Meta doesn't clearly document for Unified Login yet. If Meta asks ECHO to slow down, the background check backs off on its own (up to 10 minutes).

**How ECHO finds the shared account after login:**
1. `GET /me/assigned_instagram_accounts`.
2. If that's empty, the Instagram IDs on the token's `instagram_manage_messages` grant (via `debug_token`).
3. Then Page-linked accounts.

If several accounts were shared, ECHO uses the first.

## 7. Access levels and testing

**Two facts decide what you can test.** Both come from Meta's docs:
- *Standard Access* covers Instagram professional accounts **you own or manage**, so logging in and sending with your own account works.
- **Webhooks need the app to be published (Live)**. Messages from people with **no role on your app** need **Advanced Access**, which means **App Review + Business Verification**.

Unified Login uses the `instagram_*` permissions. Meta says Advanced Access already granted for them (e.g. under *Instagram API with Facebook Login*) carries over.

Test plan:

1. Add a second person you control or trust under **App roles → Roles**.
   - Meta's docs say testers need a role on the app and must grant its permissions.
   - **Check this step works before relying on it.**
2. **Publish the app (Live).** Meta wants a privacy policy URL first. ECHO serves one at `https://<your-tunnel>/privacy`; **edit it first** (`backend/app/api/instagram.py`) with your name and contact. Meta also wants an icon, a category and the data-deletion URL from §4.
3. **DM your account from the tester.** The conversation appears in **Messages** within seconds, with AI replies **off** and consent **"Not asked"**.
4. **Send through ECHO.**
   - Set **Consent → They agreed** and turn on **AI replies**.
   - Schedule a message, or use **Run now (test)**.

   It arrives in Instagram, after a one-time AI disclosure.
5. **Reply from the Instagram app.** ECHO records your reply as yours. ECHO never re-processes its own messages when Instagram echoes them back.
6. **Automatic replies (Phase 5).** With consent granted and AI replies on:
   - DM your account from the tester. About 45 seconds later (the friend's **Reply after** setting), ECHO answers in your voice, after a one-time disclosure.
   - Send "can you lend me RM50?" and the reply waits in **Messages → Needs you** instead of being sent.
   - Reply yourself in Instagram and ECHO steps back for 30 minutes.
   - The tester sending **STOP** turns AI replies off for them.
   - Instead of setting consent by hand, try **Ask by DM** on a friend who hasn't agreed yet; their **YES** is recorded automatically.

### App Review (Advanced Access)

This is needed only for friends who aren't app testers. Request Advanced Access for `instagram_basic` and `instagram_manage_messages`. You'll need:

- **Business Verification** of the business portfolio. Individuals often can't complete it.
- A **screencast** of the full flow.
- An honest use-case description.
- The privacy policy and data-deletion callback (both built in).

During review, Meta's reviewers open your URLs and send test events. **Your Mac must be awake, online and running `start.sh` the whole time, and a named tunnel is effectively required**, because a quick-tunnel address could change mid-review.

Meta may decline this use case. ECHO then keeps working with your own account and app testers. For everyone else it offers draft → **Copy draft / Open Instagram**.

### Older apps: legacy Instagram Login

Apps that still show **API setup with Instagram login** can set `META_LOGIN_MODE=instagram`. That mode also needs `META_INSTAGRAM_APP_ID` and the *Instagram* app secret in `META_APP_SECRET`, and works as in Phase 4 (60-day tokens, refreshed automatically). Each connected account remembers which login it used. Switching modes shows **"Connect again"** in the app instead of failing silently.

## 8. Living with a laptop server

ECHO only works while the Mac is awake, online and running `./scripts/start.sh`.

- **Sleep:** `start.sh` runs `caffeinate`, which prevents idle sleep while ECHO runs. **Closing the lid still sleeps a MacBook Air** unless it's plugged in with an external display.
- **What happens while ECHO is offline:**
  - **Incoming DMs:** Meta retries for a while, then gives up and unsubscribes after about an hour of failures. On the next start, ECHO re-subscribes. Tap **Sync recent conversations** to backfill missed threads.
  - **Scheduled messages:** anything more than 15 minutes overdue is **blocked, not sent late**, and you're notified. Recurring jobs simply continue with the next occurrence.
  - **The 24-hour window** keeps running while you're offline. A friend's window can close while the Mac is asleep.
- **Logs:** `logs/server.log` and `logs/tunnel.log` in the project folder. Run `tail -f logs/server.log` while testing.
- **Backups:** `pg_dump echo > echo-backup.sql`. Keep `backend/.env` safe, especially `ECHO_TOKEN_ENCRYPTION_KEY`.

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| doctor: *Cloudflare Tunnel ❌ no public https URL* | `start.sh` isn't running, or with a named tunnel the tunnel isn't running |
| doctor: *Database … pending migrations* | Run `cd backend && .venv/bin/python -m app.migrate` |
| doctor: *Claude API — key was rejected* | Check `ANTHROPIC_API_KEY` |
| Meta's login says the redirect URI is invalid / "URL blocked" | The Valid OAuth Redirect URI doesn't match this run's tunnel address (quick tunnels change each start) |
| Login fails with "Invalid platform app" or "Invalid Scopes" | You're on a new app with `META_LOGIN_MODE=instagram`; use `unified` with `META_APP_ID` + `META_LOGIN_CONFIG_ID` |
| doctor: *Meta rejected META_APP_ID/META_APP_SECRET* | Use App ID + App secret from **App settings → Basic** |
| "Messaging permission wasn't granted" | Add `instagram_manage_messages` to the login configuration (§4) and connect again |
| "No Instagram account was shared with ECHO" | On Meta's screen, select your Instagram professional account; check the configuration's assets include Instagram accounts and the app is in your business portfolio |
| A call fails with error 190 after working | The system-user token stopped (e.g. the app left the business portfolio) or a user-token config expired; connect again |
| New DMs only appear after a sync, and **Settings → Instagram → Last webhook from Meta** says *None yet* | Meta isn't delivering webhooks: check §5 (callback URL = your current tunnel, `messages` subscribed), that the app is **Live**, that the sender has an app role, and "Allow access to messages". Meanwhile the 30-second check picks DMs up. |
| ECHO doesn't auto-reply | Open the chat. The line under the controls says why: AI off, no consent, you replied recently ("taken over"), a held draft, the 24-hour window, or `ECHO_MESSAGING_PROVIDER=mock` (replies go nowhere). |
| Sync says Instagram asked ECHO to slow down | Meta's rate limit; wait a few minutes. Sync now asks for only 5 chats × 10 messages. |
| Dashboard test events arrive, real DMs don't | The app isn't Live, the sender has no app role (needs Advanced Access), or "Allow access to messages" is off |
| DMs stopped arriving after the Mac slept | Meta unsubscribed the webhook; restart `start.sh` (it re-subscribes), then **Sync recent conversations** |
| *Port 8000 is already in use* | Another `start.sh` is running; stop it, or set `ECHO_PORT=8001 ./scripts/start.sh` |
| Scheduled message *failed: outside Instagram's messaging window* | The friend hadn't messaged in the 24 hours before sending; expected behaviour |

Sources:
- [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
- [Cloudflare TryCloudflare limitations](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)
- [cloudflared as a macOS service](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/local-management/as-a-service/macos)
- [Meta Webhooks — delivery failures and auto-unsubscribe](https://developers.facebook.com/docs/messenger-platform/webhooks)
- [Instagram Platform — Unified Login is the recommended setup for new integrations](https://developers.facebook.com/documentation/instagram-platform)
- [Business Login for Instagram (legacy mode)](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login)
- [Send Messages](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api)
- [Webhooks for Instagram Messaging](https://developers.facebook.com/documentation/business-messaging/instagram-messaging/webhooks)
- [Conversations API](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/conversations-api)
- [Instagram Platform overview](https://developers.facebook.com/documentation/instagram-platform/overview)
