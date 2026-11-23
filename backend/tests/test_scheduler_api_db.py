"""Scheduler, friends, automation, activity and dev APIs (real PostgreSQL)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.messaging.provider import MockMessagingProvider
from tests.conftest import AUTH, requires_db

pytestmark = requires_db
KL = ZoneInfo("Asia/Kuala_Lumpur")


@pytest.fixture
def setup(make_client, db_url):
    messaging = MockMessagingProvider()
    client = make_client(database_url=db_url, messaging=messaging)
    assert client.post("/v1/dev/seed", headers=AUTH).status_code == 200
    friends = {f["display_name"].split()[0]: f for f in client.get("/v1/friends", headers=AUTH).json()["friends"]}
    return client, messaging, friends


def tomorrow_8pm_local() -> str:
    day = datetime.now(KL).date() + timedelta(days=1)
    return f"{day.isoformat()}T20:00"


def tick(client):
    return client.post("/v1/dev/worker/tick", headers=AUTH).json()


def test_seed_is_idempotent(setup):
    client, _, friends = setup
    client.post("/v1/dev/seed", headers=AUTH)
    assert len(client.get("/v1/friends", headers=AUTH).json()["friends"]) == 4
    convs = client.get("/v1/conversations", headers=AUTH).json()["conversations"]
    kenji = next(c for c in convs if c["friend"]["display_name"] == "Kenji Tanaka")
    assert [m["sender"] for m in kenji["messages"]] == ["friend", "me", "friend"]


def test_create_list_update_pause_resume_cancel(setup):
    client, _, friends = setup
    r = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": friends["Kenji"]["id"], "scheduled_at": tomorrow_8pm_local(),
        "recurrence": "weekly", "instruction": "ask if he's free this weekend",
    })
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "scheduled"
    assert job["eligibility"] == "checked_at_dispatch"
    assert job["time_zone_id"] == "Asia/Kuala_Lumpur" and job["recurrence"] == "weekly"
    assert datetime.fromisoformat(job["scheduled_at"]).astimezone(KL).hour == 20
    assert job["style"] == "close_friend" and job["consent_recorded"] is True

    patched = client.patch(f"/v1/jobs/{job['id']}", headers=AUTH, json={"instruction": "ask about ramen"}).json()
    assert patched["instruction"] == "ask about ramen"

    assert client.post(f"/v1/jobs/{job['id']}/pause", headers=AUTH).json()["status"] == "paused"
    assert client.post(f"/v1/jobs/{job['id']}/resume", headers=AUTH).json()["status"] == "scheduled"
    assert client.delete(f"/v1/jobs/{job['id']}", headers=AUTH).status_code == 204
    assert client.get(f"/v1/jobs/{job['id']}", headers=AUTH).json()["status"] == "cancelled"


@pytest.mark.parametrize("payload, message", [
    ({"scheduled_at": "2020-01-01T09:00"}, "future"),
    ({"timezone": "Mars/Base"}, "timezone"),
    ({"recurrence": "custom", "rrule": "FREQ=MINUTELY"}, "FREQ"),
])
def test_validation(setup, payload, message):
    client, _, friends = setup
    body = {"friend_id": friends["Kenji"]["id"], "scheduled_at": tomorrow_8pm_local(), "instruction": "hi"}
    body.update(payload)
    r = client.post("/v1/jobs", headers=AUTH, json=body)
    assert r.status_code == 422 and message in r.json()["detail"]


def test_run_now_sends_through_the_full_pipeline(setup):
    client, messaging, friends = setup
    job = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": friends["Kenji"]["id"], "scheduled_at": tomorrow_8pm_local(), "instruction": "say hi",
    }).json()
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    stats = tick(client)
    assert stats["sent"] == 1
    after = client.get(f"/v1/jobs/{job['id']}", headers=AUTH).json()
    assert after["status"] == "scheduled", "the original 8 PM occurrence is still pending"
    assert after["last_run_status"] is None or after["last_run_status"] in ("sent", "scheduled")
    runs = client.get(f"/v1/jobs/{job['id']}/runs", headers=AUTH).json()["runs"]
    assert runs[-1]["status"] == "sent" and runs[-1]["draft_text"]
    kenji_thread = next(c for c in client.get("/v1/conversations", headers=AUTH).json()["conversations"]
                        if c["friend"]["display_name"] == "Kenji Tanaka")
    assert kenji_thread["messages"][-1]["sender"] == "echo_scheduled"


def test_blocked_then_inbound_opens_window(setup):
    client, messaging, friends = setup
    giulia = friends["Giulia"]  # last message 30h ago, consent only requested
    job = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": giulia["id"], "scheduled_at": tomorrow_8pm_local(), "instruction": "ask about study group",
    }).json()
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    tick(client)
    blocked = client.get(f"/v1/jobs/{job['id']}", headers=AUTH).json()
    assert blocked["status"] == "scheduled" and blocked["last_run_status"] == "blocked"
    runs = client.get(f"/v1/jobs/{job['id']}/runs", headers=AUTH).json()["runs"]
    assert runs[0]["status"] == "blocked"
    assert set(runs[0]["eligibility"]["reasons"]) == {"no_consent", "outside_standard_window"}

    # She messages you and you record her consent → next run goes out.
    client.post(f"/v1/dev/friends/{giulia['id']}/inbound", headers=AUTH, json={"text": "ciao!"})
    r = client.patch(f"/v1/friends/{giulia['id']}", headers=AUTH,
                     json={"consent": "granted", "consent_evidence": "Said yes in class"})
    assert r.json()["consent"] == "granted"
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    assert tick(client)["sent"] == 1


def test_needs_attention_and_approve(setup):
    client, messaging, friends = setup
    job = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": friends["Kenji"]["id"], "scheduled_at": tomorrow_8pm_local(),
        "instruction": "remind him he owes me RM20",
    }).json()
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    tick(client)
    j = client.get(f"/v1/jobs/{job['id']}", headers=AUTH).json()
    assert j["status"] == "needs_attention" and "money" in j["failure_reason"]
    assert j["draft_text"]
    approved = client.post(f"/v1/jobs/runs/{j['latest_run_id']}", headers=AUTH, json={"action": "approve"})
    assert approved.json()["status"] == "retry_scheduled"
    assert tick(client)["sent"] == 1


def test_dismiss_needs_attention(setup):
    client, _, friends = setup
    job = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": friends["Kenji"]["id"], "scheduled_at": tomorrow_8pm_local(),
        "instruction": "promise I'll lend him money", "automation_enabled": True,
    }).json()
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    tick(client)
    run_id = client.get(f"/v1/jobs/{job['id']}", headers=AUTH).json()["latest_run_id"]
    r = client.post(f"/v1/jobs/runs/{run_id}", headers=AUTH, json={"action": "sent_manually"})
    assert r.json()["status"] == "cancelled" and r.json()["blocked_reason"] == "sent_manually"


def test_global_pause_endpoint(setup):
    client, messaging, friends = setup
    assert client.put("/v1/automation", headers=AUTH, json={"paused": True}).json()["paused"] is True
    job = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": friends["Kenji"]["id"], "scheduled_at": tomorrow_8pm_local(), "instruction": "say hi",
    }).json()
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    tick(client)
    assert messaging.sent == []
    feed = client.get("/v1/notifications", headers=AUTH).json()
    cats = [n["category"] for n in feed["notifications"]]
    assert "automation_changed" in cats and "job_blocked" in cats
    assert feed["unread_count"] == 2
    client.post("/v1/notifications/read", headers=AUTH)
    assert client.get("/v1/notifications", headers=AUTH).json()["unread_count"] == 0


def test_manual_reply_respects_window(setup):
    client, messaging, friends = setup
    ok = client.post(f"/v1/friends/{friends['Kenji']['id']}/messages", headers=AUTH, json={"text": "on my way"})
    assert ok.status_code == 201 and ok.json()["sender"] == "me"
    late = client.post(f"/v1/friends/{friends['Daniel']['id']}/messages", headers=AUTH, json={"text": "hello"})
    assert late.status_code == 409 and "24 hours" in late.json()["detail"]


def test_chat_can_schedule_a_message(setup):
    client, _, friends = setup
    r = client.post("/v1/chat/messages", headers=AUTH, json={
        "client_msg_id": str(uuid.uuid4()),
        "text": "Send Kenji a casual message at 8 PM tomorrow asking whether he's free this weekend",
    })
    assert r.status_code == 200, r.text
    action = r.json()["message"]["actions"][0]
    assert action["type"] == "scheduled_job" and action["recipient_name"] == "Kenji Tanaka"
    assert datetime.fromisoformat(action["scheduled_at"]).astimezone(KL).hour == 20
    jobs = client.get("/v1/jobs", headers=AUTH).json()["jobs"]
    assert jobs[0]["created_via"] == "chat" and "free this weekend" in jobs[0]["instruction"]
    history = client.get("/v1/chat/messages", headers=AUTH).json()["messages"]
    assert history[-1]["actions"][0]["job_id"] == action["job_id"]


def test_chat_schedule_for_unknown_friend_fails_cleanly(setup):
    client, _, _ = setup
    r = client.post("/v1/chat/messages", headers=AUTH, json={
        "client_msg_id": str(uuid.uuid4()), "text": "Send Zoe a message at 9 pm tomorrow asking about lunch",
    })
    action = r.json()["message"]["actions"][0]
    assert action["type"] == "schedule_failed" and "Zoe" in action["reason"]
    assert client.get("/v1/jobs", headers=AUTH).json()["jobs"] == []


def test_dev_routes_hidden_in_production(make_client, db_url):
    client = make_client(database_url=db_url, echo_env="production")
    assert client.post("/v1/dev/seed", headers=AUTH).status_code == 404


def test_failure_mode_toggle(setup):
    client, messaging, friends = setup
    client.put("/v1/dev/mock-provider", headers=AUTH, json={"mode": "permanent"})
    job = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": friends["Kenji"]["id"], "scheduled_at": tomorrow_8pm_local(), "instruction": "say hi",
    }).json()
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    assert tick(client)["failed"] == 1
