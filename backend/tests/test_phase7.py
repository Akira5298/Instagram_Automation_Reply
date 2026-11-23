"""Smarter replies: style learning, scheduled-message ideas, exact scheduled text,
media handling, context API and the Claude request shapes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from app.ai.compose_context import build_compose_context, render_friend_style
from app.services import media
from app.services.style_learning import clean_learned
from tests.conftest import AUTH, requires_db
from tests.fake_graph import echo, webhook_payload
from tests.test_anthropic_provider import FakeMessages, provider_with, response
from tests.test_auto_reply import ar  # noqa: F401
from tests.test_instagram import igu, post_webhook, tick  # noqa: F401

KL = ZoneInfo("Asia/Kuala_Lumpur")


# ---------- media (pure) ----------

def test_attachments_are_normalised_and_described():
    raw = [
        {"type": "ig_reel", "payload": {"url": "https://lookaside.fbsbx.com/r", "title": "cat vs cucumber",
                                        "reel_video_id": "9"}},
        {"type": "share", "payload": {"url": "https://lookaside.fbsbx.com/s"}},
        {"type": "image", "payload": {"url": "http://insecure.example/x.jpg"}},
    ]
    clean = media.normalise_attachments(raw)
    assert clean[0] == {"type": "ig_reel", "url": "https://lookaside.fbsbx.com/r", "title": "cat vs cucumber"}
    assert clean[-1]["url"] is None                         # http links are never kept
    assert media.describe(None, clean[:1], False) == "[Reel: “cat vs cucumber”]"
    assert media.describe("haha", [{"type": "audio", "url": None, "title": None}], True).startswith(
        "(replying to your story)")


def test_image_fetcher_only_talks_to_meta_cdns():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "lookaside.fbsbx.com":
            return httpx.Response(302, headers={"location": "https://evil.example/steal"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png")

    fetcher = media.ImageFetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    import asyncio

    got = asyncio.run(fetcher.fetch([
        "https://169.254.169.254/latest/meta-data",          # never requested
        "https://lookaside.fbsbx.com/ig_messaging_cdn/?a=1",  # redirect off-CDN is not followed
        "https://scontent.cdninstagram.com/v/p.png",
    ], limit=3))
    assert calls == ["https://lookaside.fbsbx.com/ig_messaging_cdn/?a=1", "https://scontent.cdninstagram.com/v/p.png"]
    assert len(got) == 1 and got[0].media_type == "image/png"


# ---------- learned style (pure) ----------

def test_learned_style_is_clamped_and_pii_free():
    learned = clean_learned({
        "tone": "teasing, warm", "formality": 9, "emoji_level": "x", "typical_length": "novel",
        "favourite_emoji": ["😭", "😭", "💀"], "phrases": ["bro", "call me 012-345 6789"],
        "humor": "roasts", "language": "en + Malay slang", "notes": "email me a@b.co",
    })
    assert learned["formality"] == 4 and learned["emoji_level"] == 2 and learned["typical_length"] == "short"
    assert learned["favourite_emoji"] == ["😭", "💀"] and learned["phrases"] == ["bro"] and learned["notes"] == ""


def test_learned_style_and_rules_render_with_rules_winning():
    out = render_friend_style({"learned": {"tone": "teasing", "phrases": ["bro", "lah"], "emoji_level": 4},
                               "tone": "gentle"})
    assert "learned tone: teasing" in out and "learned phrases the user uses with them: bro, lah" in out
    assert "rule (from the user, wins): tone: gentle" in out


def test_context_renders_calendar_and_length():
    now = datetime(2026, 11, 16, 9, 0, tzinfo=KL)
    block = build_compose_context(
        traits=[], examples=[], friend={"display_name": "Mika", "style": "partner", "language": None},
        thread=[], now=now, timezone="Asia/Kuala_Lumpur", reply_length="long",
        user_context={"place": "Kuala Lumpur, Malaysia", "location_age_min": 5, "events": [
            {"start": "2026-11-16T07:00:00+00:00", "end": "2026-11-16T08:00:00+00:00", "all_day": False,
             "busy": True}]},
    )
    assert "<reply_length>long</reply_length>" in block
    assert "today 15:00–16:00 busy" in block and "Kuala Lumpur" in block
    assert "partner" in block


# ---------- Claude request shapes ----------

async def test_draft_reply_sends_images_and_reads_alternatives():
    body = json.dumps({"action": "react", "message": "", "alternatives": ["so cute", "omg"], "language": "en",
                       "flags": [], "note": ""})
    fake = FakeMessages(response(body))
    from app.ai.provider import ImageInput

    draft = await provider_with(fake).draft_reply("<recent_thread>…</recent_thread>",
                                                  [ImageInput("image/jpeg", "aGk=")])
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert content[-1]["type"] == "text"
    schema = fake.calls[0]["output_config"]["format"]["schema"]
    assert "react" in schema["properties"]["action"]["enum"] and "alternatives" in schema["required"]
    assert draft.action == "react" and draft.alternatives == ["so cute", "omg"]


async def test_suggest_and_learn_use_structured_output():
    fake = FakeMessages(response(json.dumps({"suggestions": [{"text": "gm babe ❤️", "vibe": "sweet"}]})))
    ideas = await provider_with(fake).suggest_messages("<friend>…</friend>", "good morning", 3)
    assert ideas[0].text == "gm babe ❤️" and "<occasion>good morning</occasion>" in fake.calls[0]["messages"][0][
        "content"]
    fake = FakeMessages(response(json.dumps({"friend_style": {"tone": "chill"}, "profile_updates": []})))
    learned = await provider_with(fake).learn_style("<messages>Me: yo</messages>")
    assert learned.friend_style == {"tone": "chill"}
    assert fake.calls[0]["model"] == provider_with(fake).summary_model


# ---------- API + worker ----------

@requires_db
def test_style_is_learned_from_your_own_messages_only(ar):  # noqa: F811
    client, fake, db_url, friend_id = ar
    for i, text in enumerate(["haha bro 😭", "lol no way", "omw 💀", "bro what", "haha ok fine 😭", "ngl same"]):
        post_webhook(client, webhook_payload(echo(f"me-{i}", text)))
    tick(client)
    r = client.post(f"/v1/friends/{friend_id}/style/learn", headers=AUTH)
    assert r.status_code == 200, r.text
    learned = r.json()["learned"]
    assert learned["tone"] == "playful" and "😭" in learned["favourite_emoji"]
    assert r.json()["learned_message_count"] == 6
    with psycopg.connect(db_url) as conn:
        trait = conn.execute("select value, source from profile_traits where trait_key = 'sentence_style.length'"
                             ).fetchone()
    assert trait is not None and trait[1] == "inferred"

    # Nothing new since: the hourly pass has nothing to do.
    assert client.portal.call(_candidates, client) == []

    assert client.delete(f"/v1/friends/{friend_id}/style/learned", headers=AUTH).status_code == 204
    assert client.get(f"/v1/friends/{friend_id}/style", headers=AUTH).json()["learned"] in (None, {})


async def _candidates(client):
    from app.services.style_learning import candidates
    async with client.app.state.pool.connection() as conn:
        return await candidates(conn)


@requires_db
def test_learning_off_means_hourly_pass_skips(ar):  # noqa: F811
    client, fake, db_url, friend_id = ar
    for i in range(6):
        post_webhook(client, webhook_payload(echo(f"me-{i}", f"haha msg {i}")))
    tick(client)
    assert len(client.portal.call(_candidates, client)) == 1
    client.put("/v1/automation", headers=AUTH, json={"learn_from_chats": False})
    assert client.portal.call(_candidates, client) == []


@requires_db
def test_message_ideas_and_exact_scheduled_text(ar):  # noqa: F811
    client, fake, db_url, friend_id = ar
    client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"style": "partner"})
    r = client.post(f"/v1/friends/{friend_id}/message-suggestions", headers=AUTH,
                    json={"occasion": "good morning", "count": 3})
    assert r.status_code == 200, r.text
    ideas = r.json()["suggestions"]
    assert len(ideas) == 3 and ideas[0]["vibe"] == "sweet" and "(good morning)" in ideas[0]["text"]

    # Scheduling the exact text: no AI draft, no safety hold — still Meta's rules.
    day = datetime.now(KL).date() + timedelta(days=1)
    exact = "can you lend me $20 tmr? 🥺"                      # would normally be held as "money"
    job = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": friend_id, "scheduled_at": f"{day}T08:00", "message_text": exact}).json()
    assert job["message_text"] == exact and job["instruction"]
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    tick(client)
    assert fake.sent[-1]["text"] == exact

    # Clearing it lets ECHO write the message again.
    r = client.patch(f"/v1/jobs/{job['id']}", headers=AUTH, json={"message_text": ""})
    assert r.status_code == 200 and r.json()["message_text"] is None


@requires_db
def test_partner_and_family_presets(ar):  # noqa: F811
    client, fake, db_url, friend_id = ar
    for preset in ("partner", "family"):
        r = client.patch(f"/v1/friends/{friend_id}", headers=AUTH, json={"style": preset})
        assert r.status_code == 200 and r.json()["style"] == preset


@requires_db
def test_context_api_keeps_only_the_next_36_hours(ar):  # noqa: F811
    client, fake, db_url, friend_id = ar
    now = datetime.now(UTC)
    events = [{"start": (now + timedelta(hours=h)).isoformat(), "end": (now + timedelta(hours=h + 1)).isoformat(),
               "all_day": False, "busy": True} for h in (-30, 2, 50)]
    r = client.put("/v1/context", headers=AUTH, json={"events": events})
    assert r.status_code == 200, r.text
    assert len(client.get("/v1/context", headers=AUTH).json()["events"]) == 1
    client.put("/v1/context", headers=AUTH, json={"clear_calendar": True})
    assert client.get("/v1/context", headers=AUTH).json()["events"] in (None, [])


@requires_db
@pytest.mark.parametrize("value", ["short", "medium", "long", "adaptive"])
def test_reply_length_setting(ar, value):  # noqa: F811
    client = ar[0]
    r = client.put("/v1/automation", headers=AUTH, json={"reply_length": value})
    assert r.status_code == 200 and r.json()["reply_length"] == value
    assert client.put("/v1/automation", headers=AUTH, json={"reply_length": "huge"}).status_code == 422
