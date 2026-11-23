"""Phase 4: Instagram login, webhooks, real-provider sending and Meta callbacks,
against a fake Graph API and a real PostgreSQL."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import psycopg
import pytest

from app.core.crypto import TokenCipher, TokenCipherError, generate_key
from app.integrations.meta.instagram_client import GraphError
from app.integrations.meta.signed_request import SignedRequestError, make_signed_request, parse_signed_request
from app.services.webhook_service import split_events
from tests.conftest import AUTH, make_settings, requires_db
from tests.fake_graph import APP_SECRET, IG_ID, FakeGraph, echo, inbound, make_service, webhook_payload

KL = ZoneInfo("Asia/Kuala_Lumpur")


# ----------------------------------------------------------------- unit tests

def test_token_cipher_round_trip_and_wrong_key():
    key = generate_key()
    blob = TokenCipher(key).encrypt("IGAA-secret")
    assert b"IGAA" not in blob
    assert TokenCipher(key).decrypt(blob) == "IGAA-secret"
    with pytest.raises(TokenCipherError):
        TokenCipher(generate_key()).decrypt(blob)
    with pytest.raises(TokenCipherError):
        TokenCipher("too-short")


def test_signed_request():
    sr = make_signed_request({"algorithm": "HMAC-SHA256", "user_id": IG_ID}, APP_SECRET)
    assert parse_signed_request(sr, APP_SECRET)["user_id"] == IG_ID
    with pytest.raises(SignedRequestError):
        parse_signed_request(sr, "other-secret")
    with pytest.raises(SignedRequestError):
        parse_signed_request("garbage", APP_SECRET)


def test_graph_error_classification():
    assert GraphError(500, 2, None, "temporary").is_transient
    assert GraphError(400, 4, None, "Application request limit reached").is_transient
    assert GraphError(400, 190, None, "Invalid OAuth access token").is_auth_error
    assert GraphError(400, 10, 2018278, "This message is sent outside of allowed window.").is_outside_window
    assert not GraphError(400, 100, None, "Invalid parameter").is_transient


def test_split_events_keys():
    payload = webhook_payload(inbound("m1", "hi"), echo("m1", "hi"),
                              {"sender": {"id": "x"}, "recipient": {"id": IG_ID}, "timestamp": 1,
                               "read": {"mid": "m1"}})
    keys = [k for k, _, _ in split_events(payload)]
    assert keys[0] == "ig:m1:msg" and keys[1] == "ig:m1:echo" and keys[2].startswith("ig:other:")
    assert split_events({"object": "page", "entry": []}) == []


async def test_authorize_url():
    fake = FakeGraph()
    service = make_service(fake, make_settings(echo_public_base_url="https://echo.example.com",
                                               meta_login_mode="instagram"))
    url = urlparse(service.client.authorize_url("state123", "https://echo.example.com/v1/instagram/oauth/callback"))
    q = parse_qs(url.query)
    assert url.netloc == "www.instagram.com" and url.path == "/oauth/authorize"
    assert q["scope"] == ["instagram_business_basic,instagram_business_manage_messages"]
    assert q["redirect_uri"] == ["https://echo.example.com/v1/instagram/oauth/callback"]
    assert q["response_type"] == ["code"] and q["state"] == ["state123"]


# ------------------------------------------------------------ integration tests

def sign(body: bytes) -> dict:
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest(),
            "Content-Type": "application/json"}


def post_webhook(client, payload: dict):
    body = json.dumps(payload).encode()
    return client.post("/webhooks/meta", content=body, headers=sign(body))


def tick(client):
    return client.portal.call(client.app.state.worker.run_once)


@pytest.fixture
def ig(make_client, db_url):
    if not db_url:
        pytest.skip("needs database")
    fake = FakeGraph()
    settings = make_settings(database_url=db_url, echo_public_base_url="https://echo.example.com",
                             echo_messaging_provider="instagram", meta_instagram_app_id="123",
                             meta_login_mode="instagram",
                             echo_token_encryption_key=generate_key(), instagram_min_send_interval=0.0)
    service = make_service(fake, settings, key=settings.echo_token_encryption_key.get_secret_value())
    client = make_client(instagram_service=service, database_url=db_url, echo_public_base_url="https://echo.example.com",
                         echo_messaging_provider="instagram", meta_instagram_app_id="123",
                         meta_login_mode="instagram",
                         echo_token_encryption_key=settings.echo_token_encryption_key.get_secret_value(),
                         instagram_min_send_interval=0.0)

    def connect():
        r = client.post("/v1/instagram/connect", headers=AUTH)
        assert r.status_code == 200, r.text
        state = parse_qs(urlparse(r.json()["authorize_url"]).query)["state"][0]
        cb = client.get("/v1/instagram/oauth/callback", params={"code": "good-code#_", "state": state},
                        follow_redirects=False)
        return cb

    return client, fake, connect, db_url


@requires_db
def test_connect_flow_stores_encrypted_token_and_subscribes(ig):
    client, fake, connect, db_url = ig
    # A Phase 3 mock account exists before connecting real Instagram.
    with psycopg.connect(db_url) as conn:
        conn.execute("""insert into instagram_accounts (user_id, ig_user_id, provider, status)
                        values ('00000000-0000-0000-0000-000000000001', 'mock-x', 'mock', 'active')""")
    cb = connect()
    assert cb.status_code == 302
    assert cb.headers["location"].startswith("echo://instagram/connected?status=ok")
    assert "username=my.creator" in cb.headers["location"]

    with psycopg.connect(db_url) as conn:
        rows = conn.execute("select provider, status, access_token_ciphertext, webhook_subscribed_at "
                            "from instagram_accounts order by provider").fetchall()
    (ig_row, mock_row) = rows
    assert ig_row[1] == "active" and b"long-token" not in bytes(ig_row[2]) and ig_row[3] is not None
    assert mock_row[1] == "revoked", "the mock account is retired"
    assert ("POST", "graph.instagram.com/v26.0/me/subscribed_apps", {"subscribed_fields": "messages"}) in fake.calls

    status = client.get("/v1/instagram/status", headers=AUTH).json()
    assert status["connected"] and status["username"] == "my.creator" and status["webhooks_subscribed"]

    # State is single use.
    replay = client.get("/v1/instagram/oauth/callback", params={"code": "good-code", "state": "nope"},
                        follow_redirects=False)
    assert "status=error" in replay.headers["location"]


@requires_db
def test_connect_rejects_missing_messaging_permission_and_personal_accounts(ig):
    client, fake, connect, _ = ig
    fake.permissions = "instagram_business_basic"
    assert "status=error" in connect().headers["location"]
    fake.permissions = "instagram_business_basic,instagram_business_manage_messages"
    fake.account_type = "PERSONAL"
    assert "personal+account" in connect().headers["location"]


@requires_db
def test_user_cancelled_login(ig):
    client, *_ = ig
    r = client.get("/v1/instagram/oauth/callback", params={"error": "access_denied",
                   "error_description": "The user denied your request"}, follow_redirects=False)
    assert "status=cancelled" in r.headers["location"]


@requires_db
def test_inbound_webhook_creates_friend_opens_window_and_dedupes(ig):
    client, fake, connect, db_url = ig
    connect()
    payload = webhook_payload(inbound("mid-1", "hey are you free friday?"))
    assert post_webhook(client, payload).json()["stored"] == 1
    assert post_webhook(client, payload).json()["stored"] == 0, "Meta retries are harmless"
    assert tick(client).webhooks == 1

    friends = client.get("/v1/friends", headers=AUTH).json()["friends"]
    assert len(friends) == 1
    mika = friends[0]
    assert mika["display_name"] == "Mika Sato" and mika["username"] == "mika.s"
    assert mika["ai_reply_enabled"] is False and mika["consent"] == "unknown", "new people are never automated"
    assert mika["last_inbound_at"] is not None
    conv = client.get("/v1/conversations", headers=AUTH).json()["conversations"][0]
    assert conv["messages"][-1]["text"] == "hey are you free friday?"


@requires_db
def test_scheduled_send_goes_through_graph_api_and_echo_is_ignored(ig):
    client, fake, connect, db_url = ig
    connect()
    post_webhook(client, webhook_payload(inbound("mid-1", "hi!")))
    tick(client)
    mika = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    client.patch(f"/v1/friends/{mika['id']}", headers=AUTH,
                 json={"ai_reply_enabled": True, "consent": "granted", "style": "close_friend"})
    day = datetime.now(KL).date() + timedelta(days=1)
    job = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": mika["id"], "scheduled_at": f"{day}T20:00", "instruction": "ask if she's free friday"}).json()
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    stats = tick(client)
    assert stats.sent == 1
    assert [s["to"] for s in fake.sent] == ["igsid-mika", "igsid-mika"]  # disclosure + message
    assert fake.sent[0]["auth"] == "Bearer long-token-1"

    # Instagram echoes our own sends back; ECHO must not treat them as new.
    before = len(client.get("/v1/conversations", headers=AUTH).json()["conversations"][0]["messages"])
    post_webhook(client, webhook_payload(echo(fake.sent[1]["mid"], fake.sent[1]["text"])))
    tick(client)
    msgs = client.get("/v1/conversations", headers=AUTH).json()["conversations"][0]["messages"]
    assert len(msgs) == before and msgs[-1]["sender"] == "echo_scheduled"

    # A reply you typed in the Instagram app is recorded as yours.
    post_webhook(client, webhook_payload(echo("mid-manual", "typed on my phone")))
    tick(client)
    msgs = client.get("/v1/conversations", headers=AUTH).json()["conversations"][0]["messages"]
    assert msgs[-1]["sender"] == "me" and msgs[-1]["text"] == "typed on my phone"


@requires_db
def test_graph_errors_map_to_worker_outcomes(ig):
    client, fake, connect, _ = ig
    connect()
    post_webhook(client, webhook_payload(inbound("mid-1", "hi!")))
    tick(client)
    mika = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    client.patch(f"/v1/friends/{mika['id']}", headers=AUTH, json={"ai_reply_enabled": True, "consent": "granted"})
    day = datetime.now(KL).date() + timedelta(days=1)

    def run(instruction):
        job = client.post("/v1/jobs", headers=AUTH, json={
            "friend_id": mika["id"], "scheduled_at": f"{day}T20:00", "instruction": instruction}).json()
        client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
        return job

    fake.send_error = (500, {"message": "An unexpected error has occurred", "code": 2})
    run("say hi")
    assert tick(client).retried == 1

    fake.send_error = (400, {"message": "This message is sent outside of allowed window.", "code": 10,
                             "error_subcode": 2018278})
    job = run("say hello")
    stats = tick(client)
    assert stats.failed >= 1
    runs = client.get(f"/v1/jobs/{job['id']}/runs", headers=AUTH).json()["runs"]
    assert "messaging window" in runs[0]["failure_reason"]

    fake.send_error = (400, {"message": "Error validating access token", "code": 190})
    run("one more")
    tick(client)
    assert client.get("/v1/instagram/status", headers=AUTH).json()["status"] == "expired"


@requires_db
def test_deleted_message_loses_its_text(ig):
    client, fake, connect, _ = ig
    connect()
    post_webhook(client, webhook_payload(inbound("mid-del", "oops wrong chat")))
    tick(client)
    deleted = inbound("mid-del", "")
    deleted["message"] = {"mid": "mid-del", "is_deleted": True}
    post_webhook(client, webhook_payload(deleted))
    tick(client)
    conv = client.get("/v1/conversations", headers=AUTH).json()["conversations"][0]
    assert conv["messages"] == [], "deleted content isn't shown"


@requires_db
def test_sync_backfills_recent_threads(ig):
    client, fake, connect, _ = ig
    connect()
    r = client.post("/v1/instagram/sync", headers=AUTH, json={})
    assert r.status_code == 200 and r.json()["friends"] == 1
    conv = client.get("/v1/conversations", headers=AUTH).json()["conversations"][0]
    assert [m["sender"] for m in conv["messages"]] == ["friend", "me"]


@requires_db
def test_token_refresh(ig):
    client, fake, connect, db_url = ig
    connect()
    with psycopg.connect(db_url) as conn:
        conn.execute("update instagram_accounts set token_expires_at = now() + interval '5 days', "
                     "token_refreshed_at = now() - interval '50 days' where provider = 'instagram'")
    service = client.app.state.instagram
    assert client.portal.call(service.refresh_expiring_tokens, client.app.state.pool) == 1
    with psycopg.connect(db_url) as conn:
        expires = conn.execute("select token_expires_at from instagram_accounts where provider = 'instagram'").fetchone()[0]
    assert expires > datetime.now(UTC) + timedelta(days=50)


@requires_db
def test_disconnect(ig):
    client, fake, connect, _ = ig
    connect()
    status = client.post("/v1/instagram/disconnect", headers=AUTH, json={"delete_messages": True}).json()
    assert status["connected"] is False


@requires_db
def test_deauthorize_and_data_deletion_callbacks(ig):
    client, fake, connect, db_url = ig
    connect()
    post_webhook(client, webhook_payload(inbound("mid-1", "hi")))
    tick(client)
    sr = make_signed_request({"algorithm": "HMAC-SHA256", "user_id": IG_ID, "issued_at": 1}, APP_SECRET)

    r = client.post("/v1/instagram/data-deletion", data={"signed_request": sr})
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("https://echo.example.com/v1/instagram/data-deletion/")
    status = client.get(f"/v1/instagram/data-deletion/{body['confirmation_code']}").json()
    assert status["status"] == "completed"
    with psycopg.connect(db_url) as conn:
        assert conn.execute("select count(*) from friends").fetchone()[0] == 0
        assert conn.execute("select count(*) from ig_messages").fetchone()[0] == 0

    bad = client.post("/v1/instagram/deauthorize", data={"signed_request": sr.replace(".", "x.", 1)})
    assert bad.status_code == 400


@requires_db
def test_privacy_page(ig):
    client, *_ = ig
    page = client.get("/privacy")
    assert page.status_code == 200 and "Privacy Policy" in page.text


def test_instagram_endpoints_explain_missing_config(make_client):
    client = make_client()
    assert client.get("/health").status_code == 200



@requires_db
def test_connect_explains_missing_unified_settings(make_client, db_url):
    client = make_client(database_url=db_url)
    r = client.post("/v1/instagram/connect", headers=AUTH)
    assert r.status_code == 503
    assert "META_APP_ID" in r.json()["detail"] and "META_LOGIN_CONFIG_ID" in r.json()["detail"]
    status = client.get("/v1/instagram/status", headers=AUTH).json()
    assert status["login_mode"] == "unified" and "META_APP_ID" in status["missing_settings"]


# ------------------------------------------------------- Unified Login (default)

def test_unified_settings_and_authorize_url():
    s = make_settings(meta_app_id="999", meta_login_config_id="cfg-42")
    assert s.meta_login_mode == "unified" and s.meta_client_id == "999"
    assert s.instagram_missing_settings() == ["ECHO_TOKEN_ENCRYPTION_KEY"]
    legacy = make_settings(meta_login_mode="instagram")
    assert legacy.instagram_missing_settings() == ["META_INSTAGRAM_APP_ID", "ECHO_TOKEN_ENCRYPTION_KEY"]

    service = make_service(FakeGraph(), make_settings())
    service.client.extra_login_params = "display=popup&config_id=ignored"
    url = urlparse(service.client.authorize_url("st", "https://echo.example.com/v1/instagram/oauth/callback"))
    q = parse_qs(url.query)
    assert url.netloc == "www.facebook.com" and url.path == "/v26.0/dialog/oauth"
    assert q["client_id"] == ["999"] and q["config_id"] == ["cfg-42"] and "scope" not in q
    assert q["response_type"] == ["code"] and q["state"] == ["st"] and q["display"] == ["popup"]


async def test_unified_calls_carry_appsecret_proof():
    fake = FakeGraph()
    service = make_service(fake, make_settings())
    await service.client.send_text("EAA-system-user", IG_ID, "igsid-mika", "hey")
    method, where, params = fake.calls[-1]
    assert where == f"graph.facebook.com/v26.0/{IG_ID}/messages"
    expected = hmac.new(APP_SECRET.encode(), b"EAA-system-user", hashlib.sha256).hexdigest()
    assert params["appsecret_proof"] == expected


@pytest.fixture
def igu(make_client, db_url):
    if not db_url:
        pytest.skip("needs database")
    fake = FakeGraph()
    key = generate_key()
    common = dict(database_url=db_url, echo_public_base_url="https://echo.example.com",
                  echo_messaging_provider="instagram", meta_app_id="999", meta_login_config_id="cfg-42",
                  echo_token_encryption_key=key, instagram_min_send_interval=0.0)
    service = make_service(fake, make_settings(**common), key=key)
    client = make_client(instagram_service=service, **common)

    def connect():
        r = client.post("/v1/instagram/connect", headers=AUTH)
        assert r.status_code == 200, r.text
        assert urlparse(r.json()["authorize_url"]).netloc == "www.facebook.com"
        state = parse_qs(urlparse(r.json()["authorize_url"]).query)["state"][0]
        return client.get("/v1/instagram/oauth/callback", params={"code": "good-code", "state": state},
                          follow_redirects=False)

    return client, fake, connect, db_url


@requires_db
def test_unified_connect_send_and_no_refresh(igu):
    client, fake, connect, db_url = igu
    cb = connect()
    assert cb.headers["location"].startswith("echo://instagram/connected?status=ok"), cb.headers["location"]
    assert "username=my.creator" in cb.headers["location"]
    with psycopg.connect(db_url) as conn:
        row = conn.execute("select ig_user_id, login_mode, meta_user_id, token_expires_at, webhook_subscribed_at, "
                           "granted_scopes from instagram_accounts where provider = 'instagram'").fetchone()
    assert row[0] == IG_ID and row[1] == "unified" and row[2] == "sysuser-1"
    assert row[3] is None, "system-user tokens don't expire"
    assert row[4] is not None and "instagram_manage_messages" in row[5]
    assert any(where == f"graph.facebook.com/v26.0/{IG_ID}/subscribed_apps" for _, where, _ in fake.calls)
    assert not any("graph.instagram.com" in where for _, where, _ in fake.calls)

    status = client.get("/v1/instagram/status", headers=AUTH).json()
    assert status["connected"] and status["login_mode"] == "unified" and not status["needs_reconnect"]

    # Inbound DM → friend; scheduled message goes out over graph.facebook.com.
    post_webhook(client, webhook_payload(inbound("mid-1", "hi!")))
    tick(client)
    mika = client.get("/v1/friends", headers=AUTH).json()["friends"][0]
    client.patch(f"/v1/friends/{mika['id']}", headers=AUTH,
                 json={"ai_reply_enabled": True, "consent": "granted", "style": "close_friend"})
    day = datetime.now(KL).date() + timedelta(days=1)
    job = client.post("/v1/jobs", headers=AUTH, json={
        "friend_id": mika["id"], "scheduled_at": f"{day}T20:00", "instruction": "ask if she's free friday"}).json()
    client.post(f"/v1/jobs/{job['id']}/run-now", headers=AUTH)
    assert tick(client).sent == 1
    assert fake.sent and all(s["host"] == "graph.facebook.com" for s in fake.sent)
    assert fake.sent[0]["auth"] == "Bearer EAA-system-user"

    service = client.app.state.instagram
    assert client.portal.call(service.refresh_expiring_tokens, client.app.state.pool) == 0
    assert client.portal.call(service.ensure_webhook_subscriptions, client.app.state.pool) == 1


@requires_db
def test_unified_discovery_fallbacks_and_errors(igu):
    client, fake, connect, db_url = igu
    fake.assigned = None  # endpoint fails → granular_scopes target_ids
    assert "status=ok" in connect().headers["location"]

    fake.assigned, fake.granular_targets = [], []
    loc = connect().headers["location"]
    assert "status=error" in loc and "No+Instagram+account" in loc

    fake.assigned = [IG_ID]
    fake.unified_scopes = ["instagram_basic"]
    loc = connect().headers["location"]
    assert "status=error" in loc and "instagram_manage_messages" in loc


@requires_db
def test_unified_user_token_config_is_exchanged_for_long_lived(igu):
    client, fake, connect, db_url = igu
    fake.token_expires_in = 3600
    assert "status=ok" in connect().headers["location"]
    assert any(p.get("grant_type") == "fb_exchange_token" for _, _, p in fake.calls)


@requires_db
def test_unified_deauthorize_matches_meta_user(igu):
    client, fake, connect, db_url = igu
    connect()
    sr = make_signed_request({"algorithm": "HMAC-SHA256", "user_id": "sysuser-1", "issued_at": 1}, APP_SECRET)
    assert client.post("/v1/instagram/deauthorize", data={"signed_request": sr}).status_code == 200
    with psycopg.connect(db_url) as conn:
        status, token = conn.execute("select status, access_token_ciphertext from instagram_accounts "
                                     "where provider = 'instagram'").fetchone()
    assert status == "revoked" and token is None


@requires_db
def test_legacy_account_needs_reconnect_under_unified(igu):
    client, fake, connect, db_url = igu
    with psycopg.connect(db_url) as conn:
        conn.execute("""insert into instagram_accounts (user_id, ig_user_id, provider, status, username, login_mode)
                        values ('00000000-0000-0000-0000-000000000001', 'old-ig', 'instagram', 'active', 'old',
                                'instagram')""")
    status = client.get("/v1/instagram/status", headers=AUTH).json()
    assert status["connected"] is False and status["needs_reconnect"] is True
