import hashlib
import hmac
import json
import uuid

import pytest

from tests.conftest import AUTH

SECRET = "test-app-secret"


@pytest.fixture
def client(make_client):
    return make_client()  # no database configured


def test_health_without_database(client):
    body = client.get("/health").json()
    assert body["database"] == "not_configured"
    assert body["ai_provider"] == "mock"


def test_v1_routes_need_database(client):
    r = client.post("/v1/chat/messages", headers=AUTH, json={"client_msg_id": str(uuid.uuid4()), "text": "hi"})
    assert r.status_code == 503


def test_webhook_handshake(client):
    q = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1158201444"}
    r = client.get("/webhooks/meta", params=q)
    assert r.status_code == 200 and r.text == "1158201444"
    q["hub.verify_token"] = "wrong"
    assert client.get("/webhooks/meta", params=q).status_code == 403


def test_webhook_signature(client):
    body = json.dumps({"object": "instagram", "entry": []}).encode()
    good = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert client.post("/webhooks/meta", content=body, headers={"X-Hub-Signature-256": good}).status_code == 200
    assert client.post("/webhooks/meta", content=body,
                       headers={"X-Hub-Signature-256": "sha256=deadbeef"}).status_code == 403
    assert client.post("/webhooks/meta", content=body).status_code == 403
