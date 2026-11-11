"""End-to-end API tests against a real PostgreSQL (see conftest.py)."""

import uuid

import pytest

from app.ai.mock_provider import MockAIProvider
from app.ai.provider import AIError, AssistantTurn
from tests.conftest import AUTH, requires_db

pytestmark = requires_db


class ScriptedProvider:
    """Returns queued turns; records what it was sent."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls = []
        self.fail_next = False

    async def chat_turn(self, context_block, messages):
        self.calls.append((context_block, messages))
        if self.fail_next:
            self.fail_next = False
            raise AIError("boom")
        return self.turns.pop(0)

    async def summarize(self, previous_summary, transcript):
        return await MockAIProvider().summarize(previous_summary, transcript)


def turn(text="ok", updates=()):
    return AssistantTurn(text=text, language="en", memory_updates=list(updates), model="fake")


def send(client, text, msg_id=None):
    return client.post("/v1/chat/messages", headers=AUTH,
                       json={"client_msg_id": str(msg_id or uuid.uuid4()), "text": text})


@pytest.fixture
def client(make_client, db_url):
    return make_client(database_url=db_url)


def test_health_reports_database(client):
    assert client.get("/health").json()["database"] == "ok"


def test_turn_persists_messages_and_learns(client):
    msg_id = uuid.uuid4()
    r = send(client, "ngl I love bouldering", msg_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_message"]["id"] == str(msg_id)  # app's own id round-trips
    assert body["message"]["role"] == "assistant"
    keys = {u["key"] for u in body["message"]["memory_updates"]}
    assert {"interests.bouldering", "slang.ngl"} <= keys

    history = client.get("/v1/chat/messages", headers=AUTH).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert history["messages"][1]["memory_updates"], "learned items survive reloads"

    traits = {t["key"]: t for t in client.get("/v1/profile", headers=AUTH).json()["traits"]}
    assert traits["interests.bouldering"]["source"] == "user_stated"
    assert traits["slang.ngl"]["source"] == "inferred"
    assert traits["slang.ngl"]["confidence"] <= 0.6


def test_retry_with_same_id_is_idempotent(make_client, db_url):
    provider = ScriptedProvider(turn("first reply"))
    client = make_client(provider=provider, database_url=db_url)
    msg_id = uuid.uuid4()
    a = send(client, "hello", msg_id).json()
    b = send(client, "hello", msg_id).json()
    assert b["replayed"] is True
    assert a["message"]["id"] == b["message"]["id"]
    assert len(provider.calls) == 1
    assert len(client.get("/v1/chat/messages", headers=AUTH).json()["messages"]) == 2


def test_failed_turn_can_be_retried(make_client, db_url):
    provider = ScriptedProvider(turn("finally"))
    provider.fail_next = True
    client = make_client(provider=provider, database_url=db_url)
    msg_id = uuid.uuid4()
    failed = send(client, "hi", msg_id)
    assert failed.status_code == 502
    assert "couldn't reach" in failed.json()["detail"]
    ok = send(client, "hi", msg_id)
    assert ok.status_code == 200 and ok.json()["message"]["text"] == "finally"
    msgs = client.get("/v1/chat/messages", headers=AUTH).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_context_includes_profile_history_and_examples(make_client, db_url):
    provider = ScriptedProvider(
        turn("nice", [{"op": "set", "category": "interests", "key": "interests.ramen", "value": "Loves ramen",
                       "kind": "stated_fact", "confidence": 1}]),
        turn("cool"),
    )
    client = make_client(provider=provider, database_url=db_url)
    send(client, "I love ramen")
    assert client.post("/v1/profile/examples", headers=AUTH,
                       json={"text": "omw!! 🍜", "language": "en"}).status_code == 201
    send(client, "what should I eat")

    context, messages = provider.calls[1]
    assert "Loves ramen" in context and "key=interests.ramen" in context
    assert "omw!! 🍜" in context
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "I love ramen"


def test_deleted_trait_is_not_relearned(client):
    send(client, "ngl that was fun")
    traits = client.get("/v1/profile", headers=AUTH).json()["traits"]
    ngl = next(t for t in traits if t["key"] == "slang.ngl")
    assert client.delete(f"/v1/profile/traits/{ngl['id']}", headers=AUTH).status_code == 204

    send(client, "ngl again")
    profile = client.get("/v1/profile", headers=AUTH).json()
    assert "slang.ngl" not in {t["key"] for t in profile["traits"]}
    assert "slang.ngl" in profile["rejected_keys"]


def test_edit_add_and_delete_all(client):
    send(client, "ngl")
    trait = client.get("/v1/profile", headers=AUTH).json()["traits"][0]
    edited = client.patch(f"/v1/profile/traits/{trait['id']}", headers=AUTH,
                          json={"value": "Only says ngl with close friends"}).json()
    assert edited["source"] == "user_edited" and edited["confidence"] == 1.0

    send(client, "ngl")  # inference must not overwrite the edit
    again = {t["id"]: t for t in client.get("/v1/profile", headers=AUTH).json()["traits"]}
    assert again[trait["id"]]["value"] == "Only says ngl with close friends"

    added = client.post("/v1/profile/traits", headers=AUTH, json={"category": "tone", "value": "Polite with teachers"})
    assert added.status_code == 201 and added.json()["source"] == "user_edited"

    export = client.get("/v1/profile/export", headers=AUTH).json()
    assert any(t["value"] == "Polite with teachers" for t in export["traits"])

    assert client.delete("/v1/profile", headers=AUTH).status_code == 204
    assert client.get("/v1/profile", headers=AUTH).json()["traits"] == []


def test_examples_reject_pii(client):
    r = client.post("/v1/profile/examples", headers=AUTH, json={"text": "call me 012 345 6789"})
    assert r.status_code == 422


def test_summarisation_runs_after_threshold(make_client, db_url):
    client = make_client(database_url=db_url)  # trigger=6, keep=2 in tests
    for i in range(4):
        send(client, f"I love hobby number {i}")
    import psycopg

    with psycopg.connect(db_url) as conn:
        rows = conn.execute("select summary, message_count from chat_summaries").fetchall()
    assert len(rows) == 1
    assert rows[0][1] == 4  # 6 pending - 2 kept
    assert "hobby number 0" in rows[0][0]


def test_clear_history_keeps_profile(client):
    send(client, "I love jazz")
    assert client.delete("/v1/chat/messages", headers=AUTH).status_code == 204
    assert client.get("/v1/chat/messages", headers=AUTH).json()["messages"] == []
    assert client.get("/v1/profile", headers=AUTH).json()["traits"]


def test_auth_required(client):
    assert client.get("/v1/profile").status_code == 401
    assert client.get("/v1/profile", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_pagination(client):
    for i in range(3):
        send(client, f"m{i}")
    page = client.get("/v1/chat/messages?limit=4", headers=AUTH).json()
    assert page["has_more"] is True and len(page["messages"]) == 4
    older = client.get("/v1/chat/messages", headers=AUTH,
                       params={"limit": 4, "before": page["messages"][0]["created_at"]}).json()
    assert older["has_more"] is False and len(older["messages"]) == 2
