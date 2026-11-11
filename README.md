# ECHO — AI personal messaging assistant (iOS)

**Phase 3: Scheduler.** Scheduled messages now run on your server. A database-backed worker turns each schedule into timed runs. For every run it writes the message in your voice with Claude, applies a safety gate, and checks Instagram's rules right before sending. Delivery goes through a **mock Instagram** provider until Phase 4.

Every attempt is recorded as sent, blocked, needs attention or failed, and you get an activity notice for each. You can also create jobs from AI Chat ("Send Kenji a message at 8 PM tomorrow asking if he's free").

Design: `docs/ARCHITECTURE.md` (see §3.3–3.5 for the scheduler).

```
backend/app/
├── worker/runner.py           # the worker: materialise → claim → draft → gates → send → notify
├── worker/__main__.py         # `python -m app.worker` (optional separate process)
├── domain/recurrence.py       # IANA timezone + DST-safe RRULE engine
├── domain/safety.py           # deterministic money/commitment/sensitive/emotional gate
├── domain/messaging_window.py # Meta 24h / consent / pause eligibility (unchanged)
├── messaging/provider.py      # MessagingProvider + MockMessagingProvider (failure simulation)
├── services/scheduler_service.py, services/notifications.py
├── api/jobs.py · friends.py · dev.py
└── supabase/migrations/0003_scheduler.sql
ios/Echo/
├── Services/MessagingBackend.swift   # Remote (your server) + Mock (offline) backends
├── Services/MessagingStore.swift     # observable friends / jobs / activity
└── Features/Scheduler/               # timeline, JobDetailSheet (history, approve, copy), ActivitySheet
```

## Upgrade from Phase 2 (5 minutes)

1. **Apply the new migration.** Your Phase 2 database needs it.
   - Local PostgreSQL:
     ```bash
     psql echo -f backend/supabase/migrations/0003_scheduler.sql
     ```
   - Supabase: in **SQL Editor**, paste and run `0003_scheduler.sql`.
2. **Update the backend's Python packages.** This adds `python-dateutil` and `tzdata`.
   ```bash
   cd backend && source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Add the scheduler settings to `.env`** (the defaults shown are fine):
   ```bash
   ECHO_ENV=development            # enables the mock-Instagram dev tools
   ECHO_MESSAGING_PROVIDER=mock
   ECHO_RUN_WORKER=true            # worker runs inside the API process
   ```
4. **Restart the server.**
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```
   - `/health` should now report `"version": "0.3.0"`.
   - The log should show `worker started`.
5. **Rebuild the app.**
   ```bash
   cd ios && xcodegen generate
   ```
   Then press ⌘R in Xcode.

### First, check that Claude really works

You haven't made a real Claude request yet, so do that before testing the scheduler. Make sure `ECHO_AI_PROVIDER=anthropic` is set, then run:

```bash
curl -s -X POST localhost:8000/v1/chat/messages \
  -H "Authorization: Bearer $ECHO_API_TOKEN" -H "Content-Type: application/json" \
  -d "{\"client_msg_id\":\"$(uuidgen)\",\"text\":\"hey! ngl I love bouldering\"}"
```

You should get a natural reply. It shouldn't start with `[mock]`, and it should include a `memory_updates` list.

If it fails, `detail` explains why: a bad API key, a rate limit, or a model the account can't use. Paste the server log line here and I'll sort it out.

## Try the scheduler

**In the app:** go to Settings → AI & backend → **Load sample friends (mock Instagram)**. This creates four friends on your server:

| Friend | Setup | What happens to a message for them |
|---|---|---|
| **Kenji** | Consent granted, messaged you 2 hours ago | Sends |
| **Giulia** | Consent only requested, last message 30 hours ago | **Blocked** (no consent, outside the 24-hour window) |
| **Mei** | AI replies off, consent not asked | **Blocked** (automation off for this friend, no consent) |
| **Daniel** | Consent not asked, last message 3 days ago | **Blocked** (no consent, outside the 24-hour window) |

Things to try:

1. **Create a job.** In Scheduler, tap **+**, pick Kenji, set a time 2 minutes from now and an instruction, then wait. The job turns **Sent**, and the message appears in Messages → Kenji marked "Scheduled by ECHO". The first message is preceded by the AI disclosure.
2. **Test without waiting.** Touch and hold any job → **Run now (test)**. It goes through exactly the same checks.
3. **See a block.** Schedule a message for Giulia. It's **Blocked**, and the card explains why. Tap the card for the delivery history, then **Copy draft** → **Open Instagram**.
4. **Unblock it.**
   - In Messages → Giulia, tap 🧪 → **Simulate a message from Giulia**. This opens the 24-hour window.
   - Set Consent to **They agreed**.
   - Run the job again. It sends.
5. **Hit the safety gate.** Use the instruction "remind him he owes me RM20". The job goes to **Review**. Open it, then **Send anyway** or **Dismiss**.
6. **Pause everything.** Tap **Pause all** in Messages. Every send is blocked, and ECHO doesn't spend any Claude tokens on drafts.
7. **Schedule from chat.** In AI Chat, say *"Send Kenji a casual message at 8 PM tomorrow asking whether he's free this weekend."* A confirmation card appears under the reply. Tap it to open the Scheduler.
8. **Check the activity feed.** The 🔔 in the Scheduler shows sent, blocked and failed events.

**Simulate Instagram failures** from the terminal (development only):

```bash
curl -X PUT localhost:8000/v1/dev/mock-provider -H "Authorization: Bearer $ECHO_API_TOKEN" \
  -H "Content-Type: application/json" -d '{"mode":"transient_once"}'   # or permanent | unknown | none
```

- `transient_once` retries with backoff and then sends.
- `permanent` fails and notifies you.
- `unknown` fails and is **never** retried, to avoid sending a duplicate.

## Tests

```bash
cd backend
ECHO_TEST_DATABASE_URL=postgresql://localhost/echo_test pytest -q   # 103 passed
```

The suite includes:
- DST and timezone recurrence, including New York's spring-forward gap and fall-back repeat
- every worker outcome, with a controllable clock
- crash recovery
- two workers racing without double-sending
- skipping missed occurrences after downtime
- end-to-end API flows, including chat-created jobs

In Xcode, ⌘U runs 26 tests, including decoding the backend's job, conversation and chat-action JSON.

## Running the worker separately (optional)

By default the worker runs inside the API process, which suits a single Railway or Render service. To scale them separately:
1. Set `ECHO_RUN_WORKER=false` on the API.
2. Run `python -m app.worker` as a second service.

Several workers can run at once safely: claims use `FOR UPDATE SKIP LOCKED`, and every send has an idempotency key.

## What's real vs. mock after Phase 3

| Piece | Status |
|---|---|
| Claude chat, memory, summaries (Phase 2) | Real |
| Scheduling: timezones, recurrence, worker, retries, crash recovery | **Real** |
| Eligibility (24h window, consent, pause), safety gate, AI disclosure | **Real**, enforced by code |
| Activity feed / notification outbox | **Real** (push delivery in Phase 6) |
| Instagram delivery | **Mock** provider (Phase 4: Graph API) |
| Friends & conversations | Seeded sample data on your server (Phase 4: real Instagram DMs) |
