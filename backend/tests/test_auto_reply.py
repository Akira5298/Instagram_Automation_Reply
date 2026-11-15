"""Phase 5: autonomous replies to inbound DMs, end to end — signed webhooks in,
worker tick, fake Graph API out — against a real PostgreSQL."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.ai.compose_context import build_compose_context, render_friend_style
from app.services.auto_reply import keyword
from app.worker.auto_reply import _primary_reason, _unanswered
from tests.conftest import AUTH, requires_db
from tests.fake_graph import echo, inbound, webhook_payload
from tests.test_instagram import igu, post_webhook, tick  # noqa: F401  (fixture + helpers)


# ----------------------------------------------------------------- unit tests

def test_consent_keywords_are_exact_and_forgiving():
    assert keyword("STOP") == "stop"
    assert keyword("stop!!") == "stop"
    assert keyword("  Stop AI ") == "stop"
    assert keyword("Berhenti") == "stop"
    assert keyword("don't stop believing") is None
    assert keyword("stop it lol") is None
    assert keyword("Yes!! 👍") == "yes"
    assert keyword("ok boleh") == "yes"
    assert keyword("yes but only on weekends") is None
    assert keyword(None) is None


def test_unanswered_marking_and_style_rendering():
    thread = [
        {"direction": "inbound", "content": "old"},
        {"direction": "outbound", "content": "my reply"},
        {"direction": "inbound", "content": "new 1"},
        {"direction": "inbound", "content": "new 2"},
    ]
    assert _unanswered(thread) == ["new 1", "new 2"]
    ctx = build_compose_context(traits=[], examples=[], friend={"display_name": "Mika", "style": "close_friend"},
                                thread=thread, now=datetime.now(UTC), timezone="Asia/Kuala_Lumpur",
                                friend_style={"emoji_level": 0, "boundaries": {"avoid_topics": ["exams"]},
                                              "custom_instructions": "keep it <short>"},
                                memories=["loves ramen"], mark_unanswered=True)
    assert "Friend: old" in ctx and "Friend [unanswered]: new 2" in ctx
    assert "never bring up: exams" in ctx and "emoji: none" in ctx
    assert "&lt;short&gt;" in ctx or "<short>" not in ctx   # user text is escaped
    assert "<friend_memories>" in ctx
    assert render_friend_style({}) == "(no extra rules)"
    assert _primary_reason({"emotional", "money"}) == "money"


# ------------------------------------------------------------- end to end

@pytest.fixture
def ar(igu):  # noqa: F811
    """Connected Instagram account + Mika, who has agreed and has AI replies on (no delay)."""
    client, fake, connect, db_url = igu
    assert "status=ok" in connect().headers["location"]
    post_webhook(client, webhook_payload(inbound("mid-0", "hey it's mika")))
    tick(client)
    mika = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    assert mika["ai_reply_enabled"] is False and mika["consent"] == "unknown"
    r = client.patch(f"/v1/friends/{mika['id']}", headers=AUTH, json={
        "ai_reply_enabled": True, "consent": "granted", "style": "close_friend", "reply_delay_seconds": 0})
    assert r.status_code == 200 and r.json()["reply_delay_seconds"] == 0
    fake.sent.clear()
    return client, fake, db_url, mika["id"]


def dm(client, mid: str, text: str, ts: int | None = None):
    post_webhook(client, webhook_payload(inbound(mid, text, ts=ts)))


def replies(db_url: str) -> list[tuple]:
    with psycopg.connect(db_url) as conn:
        return conn.execute("select inbound_message_id, status, reason from auto_replies order by created_at").fetchall()


def make_due(db_url: str) -> None:
    with psycopg.connect(db_url) as conn:
        conn.execute("update auto_replies set due_at = now() - interval '1 second' where status = 'pending'")


@requires_db
def test_reply_is_sent_with_disclosure_once_and_echoes_are_ignored(ar):
    client, fake, db_url, friend_id = ar
    dm(client, "mid-1", "how was the gig")
    stats = tick(client)
    assert stats.replies_sent == 1
    texts = [s["text"] for s in fake.sent]
    assert texts[0].startswith("Some replies in this chat may be written by my AI assistant")
    assert texts[1] == "[mock] haha how was the gig 😄"

    with psycopg.connect(db_url) as conn:
        ledger = conn.execute("select reply_to_inbound_id, status from outbound_messages").fetchall()
        assert ledger == [("mid-1", "sent")]
        senders = [r[0] for r in conn.execute(
            "select sent_by from ig_messages where direction = 'outbound' order by platform_time").fetchall()]
        assert senders == ["echo_auto", "echo_auto"]

    # Meta echoes our own sends back: recognised, so no takeover and no reply loop.
    post_webhook(client, webhook_payload(*[echo(s["mid"], s["text"]) for s in fake.sent]))
    tick(client)
    friend = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    assert friend["manual_override_until"] is None

    # Second reply: no second disclosure; "lol" needs no answer.
    dm(client, "mid-2", "and the food?")
    tick(client)
    assert [s["text"] for s in fake.sent][-1] == "[mock] haha and the food? 😄"
    assert len(fake.sent) == 3
    dm(client, "mid-3", "lol")
    assert tick(client).replies_skipped == 1
    assert len(fake.sent) == 3
    assert replies(db_url)[-1] == ("mid-3", "skipped", "no_reply_needed")
    msgs = client.get("/v1/conversations", headers=AUTH).json()["conversations"][0]["messages"]
    assert "echo_auto" in {m["sender"] for m in msgs}


@requires_db
def test_burst_of_messages_gets_one_reply(ar):
    client, fake, db_url, friend_id = ar
    client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"reply_delay_seconds": 60})
    for i, text in enumerate(["omg", "guess what", "i got the job"]):
        dm(client, f"burst-{i}", text)
    tick(client)                       # not due yet
    assert fake.sent == []
    assert [r[1] for r in replies(db_url)] == ["superseded", "superseded", "pending"]
    make_due(db_url)
    assert tick(client).replies_sent == 1
    assert fake.sent[-1]["text"] == "[mock] haha i got the job 😄"


@requires_db
def test_safety_gate_holds_money_then_you_edit_and_send(ar):
    client, fake, db_url, friend_id = ar
    dm(client, "mid-m", "can you lend me RM50 till friday")
    stats = tick(client)
    assert stats.replies_held == 1 and fake.sent == []
    reviews = client.get("/v1/reviews", headers=AUTH).json()["reviews"]
    assert len(reviews) == 1
    item = reviews[0]
    assert item["origin"] == "auto_reply" and item["reason"] == "money"
    assert item["inbound_text"] == "can you lend me RM50 till friday" and item["draft"]
    acts = client.get("/v1/notifications", headers=AUTH).json()["notifications"]
    assert acts[0]["category"] == "needs_attention"

    r = client.post(f"/v1/reviews/{item['id']}", headers=AUTH,
                    json={"action": "send", "text": "sorry, can't this week"})
    assert r.status_code == 200, r.text
    assert fake.sent[-1]["text"] == "sorry, can't this week"
    assert client.get("/v1/reviews", headers=AUTH).json()["count"] == 0
    assert replies(db_url)[-1][1] == "sent"
    # Sending twice is impossible: the review is closed and the mid is used.
    assert client.post(f"/v1/reviews/{item['id']}", headers=AUTH, json={"action": "send"}).status_code == 409


@requires_db
def test_questions_only_you_can_answer_and_review_first(ar):
    client, fake, db_url, friend_id = ar
    dm(client, "mid-q", "are you free friday?")
    tick(client)
    item = client.get("/v1/reviews", headers=AUTH).json()["reviews"][0]
    assert item["reason"] in ("needs_you", "unknown_info")
    assert client.post(f"/v1/reviews/{item['id']}", headers=AUTH, json={"action": "dismiss"}).json()["status"] == "dismissed"
    assert replies(db_url)[-1][1:] == ("cancelled", "dismissed")

    client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"review_first": True})
    dm(client, "mid-r", "nice weather today")
    tick(client)
    item = client.get("/v1/reviews", headers=AUTH).json()["reviews"][0]
    assert item["reason"] == "review_first" and fake.sent == []
    r = client.post(f"/v1/reviews/{item['id']}", headers=AUTH, json={"action": "sent_manually"})
    assert r.json()["status"] == "sent_manually"


@requires_db
def test_attachments_are_never_answered_blind(ar):
    client, fake, db_url, friend_id = ar
    post_webhook(client, webhook_payload({
        "sender": {"id": "igsid-mika"}, "recipient": {"id": "17841400000000001"},
        "timestamp": int(time.time() * 1000),
        "message": {"mid": "mid-img", "attachments": [{"type": "image", "payload": {"url": "https://x"}}]}}))
    tick(client)
    assert fake.sent == []
    assert client.get("/v1/reviews", headers=AUTH).json()["reviews"][0]["reason"] in ("attachment", "needs_you")


@requires_db
def test_you_replying_yourself_makes_echo_step_back(ar):
    client, fake, db_url, friend_id = ar
    client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"reply_delay_seconds": 60})
    dm(client, "mid-a", "you coming tonight?")
    tick(client)
    # You answer from the Instagram app before ECHO's delay is up.
    post_webhook(client, webhook_payload(echo("mid-mine", "yes! 8pm")))
    tick(client)
    assert replies(db_url)[-1][1:] == ("cancelled", "you_replied")
    friend = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    assert friend["manual_override_until"] is not None

    # While you're in the chat, new messages are not auto-answered.
    dm(client, "mid-b", "cool see you")
    tick(client)          # webhook processed → reply queued (due in 60 s)
    make_due(db_url)
    tick(client)
    assert replies(db_url)[-1][1:] == ("skipped", "you_took_over")
    assert fake.sent == []

    # Hand back → replies resume.
    assert client.delete(f"/v1/friends/{friend_id}/takeover", headers=AUTH).json()["manual_override_until"] is None
    dm(client, "mid-c", "what should i bring")
    tick(client)
    make_due(db_url)
    tick(client)
    assert fake.sent and fake.sent[-1]["text"].startswith("[mock]")

    # Explicit takeover cancels anything waiting.
    client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"reply_delay_seconds": 60})
    dm(client, "mid-d", "ok")
    tick(client)
    r = client.post(f"/v1/friends/{friend_id}/takeover", headers=AUTH, json={"minutes": 90})
    assert r.status_code == 200 and r.json()["manual_override_until"]
    assert replies(db_url)[-1][1:] == ("cancelled", "you_took_over")


@requires_db
def test_stop_keyword_revokes_consent_and_nothing_is_sent(ar):
    client, fake, db_url, friend_id = ar
    dm(client, "mid-stop", "STOP")
    tick(client)
    friend = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    assert friend["consent"] == "revoked" and friend["ai_reply_enabled"] is False
    assert fake.sent == [] and replies(db_url) == []
    with psycopg.connect(db_url) as conn:
        assert conn.execute("select status, method, evidence from consent_events order by recorded_at desc"
                            ).fetchone() == ("revoked", "dm_keyword_stop", "mid-stop")
    titles = [n["title"] for n in client.get("/v1/notifications", headers=AUTH).json()["notifications"]]
    assert any("turned off AI replies" in t for t in titles)
    # You can't re-ask someone who said STOP from ECHO.
    assert client.post(f"/v1/friends/{friend_id}/consent-request", headers=AUTH).status_code == 409


@requires_db
def test_consent_request_and_yes_reply(igu):  # noqa: F811
    client, fake, connect, db_url = igu
    connect()
    dm(client, "mid-hi", "heyy")
    tick(client)
    friend = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    fake.sent.clear()
    r = client.post(f"/v1/friends/{friend['id']}/consent-request", headers=AUTH)
    assert r.status_code == 200 and r.json()["consent"] == "requested"
    assert "Reply YES to allow" in fake.sent[-1]["text"]

    dm(client, "mid-yes", "Yes!")
    tick(client)
    friend = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    assert friend["consent"] == "granted"
    assert len(fake.sent) == 1             # ECHO doesn't answer the YES
    with psycopg.connect(db_url) as conn:
        assert conn.execute("select status, evidence from consent_events order by recorded_at desc"
                            ).fetchone() == ("granted", "mid-yes")


@requires_db
def test_limits_pause_and_late_messages(ar):
    client, fake, db_url, friend_id = ar
    client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"daily_reply_limit": 1})
    dm(client, "lim-1", "first")
    tick(client)
    dm(client, "lim-2", "second")
    tick(client)
    assert replies(db_url)[-1][1:] == ("blocked", "daily_limit")
    client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"daily_reply_limit": 20})

    r = client.put("/v1/automation", headers=AUTH, json={"paused": True})
    assert r.json()["paused"] is True
    dm(client, "pause-1", "u there")
    tick(client)
    assert replies(db_url)[-1][1:] == ("skipped", "global_pause")
    client.put("/v1/automation", headers=AUTH, json={"paused": False, "takeover_minutes": 10})
    assert client.get("/v1/automation", headers=AUTH).json()["takeover_minutes"] == 10


@requires_db
def test_messages_missed_while_offline_are_not_answered_late(ar):
    client, fake, db_url, friend_id = ar
    with psycopg.connect(db_url) as conn:   # the fixture's first DM happened earlier still
        conn.execute("update ig_messages set platform_time = now() - interval '4 hours'")
    old = int((time.time() - 3 * 3600) * 1000)
    dm(client, "late-1", "morning!", ts=old)
    tick(client)
    assert replies(db_url)[-1][1:] == ("blocked", "too_late")


@requires_db
def test_transient_errors_retry_and_turning_ai_off_cancels(ar):
    client, fake, db_url, friend_id = ar
    fake.send_error = (500, {"message": "temporary", "code": 2})
    dm(client, "mid-t", "hello there")
    assert tick(client).replies_sent == 0
    with psycopg.connect(db_url) as conn:
        status, attempt, nxt = conn.execute(
            "select status, attempt, next_attempt_at from auto_replies where inbound_message_id = 'mid-t'").fetchone()
    assert status == "pending" and attempt == 1 and nxt > datetime.now(UTC)
    fake.send_error = None
    with psycopg.connect(db_url) as conn:
        conn.execute("update auto_replies set next_attempt_at = now() - interval '1 second'")
    assert tick(client).replies_sent == 1

    client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"reply_delay_seconds": 60})
    dm(client, "mid-u", "another")
    tick(client)
    client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"ai_reply_enabled": False})
    assert replies(db_url)[-1][1:] == ("cancelled", "settings_changed")


@requires_db
def test_friend_style_round_trip(ar):
    client, fake, db_url, friend_id = ar
    assert client.get(f"/v1/friends/{friend_id}/style", headers=AUTH).json()["formality"] == 2
    r = client.put(f"/v1/friends/{friend_id}/style", headers=AUTH, json={
        "tone": "teasing", "emoji_level": 4, "avoid_topics": ["exams", " "], "custom_instructions": "call her Mik"})
    assert r.status_code == 200
    assert r.json()["avoid_topics"] == ["exams"] and r.json()["emoji_level"] == 4
    assert client.put(f"/v1/friends/{friend_id}/style", headers=AUTH, json={"formality": 9}).status_code == 422


@requires_db
def test_expired_window_closes_review(ar):
    client, fake, db_url, friend_id = ar
    dm(client, "mid-x", "can you pay me back")
    tick(client)
    assert client.get("/v1/reviews", headers=AUTH).json()["count"] == 1
    with psycopg.connect(db_url) as conn:
        conn.execute("update friends set last_inbound_message_at = now() - interval '25 hours'")
    assert client.get("/v1/reviews", headers=AUTH).json()["count"] == 0
    assert replies(db_url)[-1][1:] == ("blocked", "outside_standard_window")
    assert timedelta  # keep import used


@requires_db
def test_offline_mock_mode_simulated_message_gets_auto_reply(make_client, db_url):
    from app.messaging.provider import MockMessagingProvider

    messaging = MockMessagingProvider()
    client = make_client(database_url=db_url, messaging=messaging)
    client.post("/v1/dev/seed", headers=AUTH)
    friend = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    client.patch(f"/v1/friends/{friend['id']}", headers=AUTH,
                 json={"ai_reply_enabled": True, "consent": "granted", "reply_delay_seconds": 0})
    r = client.post(f"/v1/dev/friends/{friend['id']}/inbound", headers=AUTH, json={"text": "how's it going"})
    assert r.json()["auto_reply"] == "queued"
    stats = client.post("/v1/dev/worker/tick", headers=AUTH).json()
    assert stats["replies_sent"] == 1
    thread = next(c for c in client.get("/v1/conversations", headers=AUTH).json()["conversations"]
                  if c["id"] == friend["id"])
    assert thread["messages"][-1]["sender"] == "echo_auto"
