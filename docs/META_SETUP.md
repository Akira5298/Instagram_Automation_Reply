# Connecting ECHO to Instagram — Meta setup guide

*Phase 4 · checked against Meta's developer docs in November 2026. Meta changes dashboards often, so menu names may differ slightly.*

## 0. What you need

- An Instagram account switched to **Creator** or **Business**. In Instagram: Settings → Account type and tools → Switch to professional account.
  - ECHO uses the *Instagram API with Instagram Login*, so **no Facebook Page is needed**.
  - Personal accounts are not supported by Meta's API.
- A Meta developer account at <https://developers.facebook.com>.
- Your backend reachable over **https**. Meta refuses plain http for both the login redirect and webhooks. Pick one:
  - **Deploy to Railway** (recommended — see §6), *or*
  - for quick local tests, a tunnel: `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`. Tunnel URLs change on every restart, so you'd have to update the dashboard each time.
- In the Instagram app: Settings → Messages and story replies → Message controls → **Connected tools → Allow access to messages = ON**. Without this, no message webhooks arrive.

## 1. Create the Meta app

1. Go to developers.facebook.com → **My Apps → Create app**.
2. Use case: **"Manage messaging & content on Instagram"**. If asked for a type, choose **Business**.
3. Open **Instagram → API setup with Instagram login**. Note two values:
   - **Instagram app ID** → `META_INSTAGRAM_APP_ID`
   - **Instagram app secret** → `META_APP_SECRET`

   Use the Instagram app ID and secret shown on this page, **not** the Meta (Facebook) app ID shown at the top of the dashboard.

## 2. Server settings (`.env` or Railway variables)

```bash
ECHO_PUBLIC_BASE_URL=https://<your-backend-host>          # no trailing slash
META_INSTAGRAM_APP_ID=<Instagram app ID>
META_APP_SECRET=<Instagram app secret>
META_WEBHOOK_VERIFY_TOKEN=<any long random string you choose>
ECHO_TOKEN_ENCRYPTION_KEY=<python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())">
ECHO_MESSAGING_PROVIDER=instagram
```

Keep `ECHO_TOKEN_ENCRYPTION_KEY` safe and stable. If you lose or change it, ECHO can no longer read the stored Instagram tokens, and you'll have to reconnect.

Restart the server. `GET /health` should show `"version": "0.4.0"`.

## 3. Business login settings

Under **Instagram → API setup with Instagram login → 3. Set up Instagram business login → Business login settings**:

| Field | Value |
|---|---|
| OAuth redirect URIs | `https://<host>/v1/instagram/oauth/callback` |
| Deauthorize callback URL | `https://<host>/v1/instagram/deauthorize` |
| Data deletion request URL | `https://<host>/v1/instagram/data-deletion` |

The redirect URI must match exactly: same scheme, host and path, with no trailing slash.

## 4. Webhooks

1. Under **Instagram → API setup with Instagram login → 2. Configure webhooks**, set:
   - **Callback URL:** `https://<host>/webhooks/meta`
   - **Verify token:** the same value as `META_WEBHOOK_VERIFY_TOKEN`
2. Click **Verify and save**. Your server answers Meta's challenge automatically.
3. Subscribe to the **`messages`** field.

The account-level subscription happens automatically: when you connect Instagram from the app, ECHO calls `POST /me/subscribed_apps?subscribed_fields=messages`. Settings → Instagram shows **Webhooks: Subscribed** when that worked.

Meta's dashboard test events reaching your server does **not** prove that real DMs will arrive. Real ones also need the account subscription above, the Allow access to messages toggle from §0, and the app being published (§5).

## 5. Access levels and testing

**Two facts decide what you can test.** Both come from Meta's docs:

- *Standard Access* is for apps serving Instagram professional accounts **you own or manage**. That covers your own account, so you can **log in and send** with it.
- **Webhooks need the app to be published (Live)**. Messages from people with **no role on your app** require **Advanced Access**, which means passing **App Review** and **Business Verification**.

### Step-by-step test plan

1. In the app, go to **Settings → Instagram → Connect Instagram** and log in with your Creator account.
2. Under **App roles → Roles**, add a second person you control or trust as a **tester**.
   - Meta's docs say testers need a role on the app and must grant the app's permissions.
   - How Instagram-only testers are added changes between dashboard versions. Follow whatever the Roles page offers, and expect to log that account in once through Business Login.
   - **Verify this step works before relying on it.** If real DMs from your tester never arrive while dashboard test events do, the remaining path is Advanced Access (below).
3. **Publish the app** (switch to Live). Meta requires these first:
   - a Privacy Policy URL — ECHO serves one at `https://<host>/privacy`; **edit it first** with your name and contact email
   - an app icon
   - a category
   - the data-deletion URL from §3
4. From the tester account, DM your Creator account. Within a few seconds the conversation appears in **Messages**. The new person starts with AI replies **off** and consent **"Not asked"**.
5. In that conversation:
   - set **Consent → They agreed**
   - turn **AI replies** on
   - schedule a message, or use **Run now (test)**

   It arrives in their Instagram inbox, preceded once by the AI disclosure.
6. Reply from your phone's Instagram app. ECHO records it as yours, using the `is_echo` webhook. ECHO's own messages are never processed twice: it ignores the echoes of what it sent.

### Applying for Advanced Access (App Review)

This is needed only if ECHO should work with friends who aren't app testers. Under **App Review → Permissions and features**, request Advanced Access for:
- `instagram_business_basic`
- `instagram_business_manage_messages`

You'll need:
- **Business Verification** of a legal entity. Individuals often can't complete this. This is the most common blocker for personal projects.
- A **screencast** showing the full flow: login → a DM arriving → a reply being sent → the controls (consent, per-friend toggle, emergency pause, disclosure, data deletion).
- A clear **use-case description**. Be honest: a personal assistant that drafts and sends replies on behalf of the account owner, only to people who messaged first, within the 24-hour window, with disclosure and opt-out.
- The privacy policy and data-deletion callback (already built in).

**Be realistic:** Meta may decline a "reply to friends as me" use case. If it does, ECHO keeps working in a compliant reduced mode:
- Full automation still works for your own account and app testers (Standard Access).
- For everyone else: draft + notification + **Copy draft / Open Instagram**, which is already built into the Scheduler.

## 6. Deploying the backend to Railway

1. Push the `backend/` folder to a GitHub repo (or use the Railway CLI).
2. Railway → **New project → Deploy from GitHub**. Set the root directory to `backend`. Railway detects Python from `requirements.txt` and starts the server with the included `Procfile`.
3. Add the variables:
   - the §2 settings
   - `DATABASE_URL` — your Supabase **session pooler** URI
   - `ECHO_API_TOKEN`
   - `ECHO_AI_PROVIDER=anthropic`
   - `ANTHROPIC_API_KEY`
   - `ECHO_ENV=production`
4. **Settings → Networking → Generate domain.** Use that https URL as `ECHO_PUBLIC_BASE_URL`, in the Meta dashboard, and in the iOS app (Settings → AI & backend → Backend URL).
5. Run the migrations against Supabase if you haven't: `0001` → `0004`.

`ECHO_ENV=production` disables the mock-Instagram dev tools.

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Login page says redirect URI is invalid | The dashboard redirect URI doesn't exactly match `ECHO_PUBLIC_BASE_URL` + `/v1/instagram/oauth/callback` |
| "Messaging permission wasn't granted" | The messages permission was unticked during login; reconnect and allow it |
| "This is a personal account" | Switch the Instagram account to Creator/Business |
| Webhooks: Not subscribed | The app isn't set up for Instagram Login webhooks, or the token lacks `instagram_business_manage_messages`; check *Last error* in Settings |
| Test events arrive but real DMs don't | The app isn't published (Live); the sender has no app role (needs Advanced Access); or "Allow access to messages" is off |
| Scheduled message *failed: outside Instagram's messaging window* | The friend hadn't messaged in the last 24 h when it was sent; expected behaviour |
| Settings shows *Instagram login expired* | The token wasn't refreshed in time (the worker refreshes when fewer than 10 days remain); reconnect |

Sources:
- [Business Login for Instagram](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login)
- [Send Messages](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api)
- [Webhooks for Instagram Messaging](https://developers.facebook.com/documentation/business-messaging/instagram-messaging/webhooks)
- [Webhook examples](https://developers.facebook.com/docs/instagram-platform/webhooks/examples/)
- [Refresh Access Token](https://developers.facebook.com/documentation/instagram-platform/reference/refresh_access_token)
- [Conversations API](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/conversations-api)
- [User Profile API](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api/user-profile)
- [Instagram Platform overview](https://developers.facebook.com/documentation/instagram-platform/overview)
