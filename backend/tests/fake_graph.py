"""A fake Instagram Graph API for tests (httpx.MockTransport)."""

from __future__ import annotations

import itertools
import json
from urllib.parse import parse_qs

import httpx

from app.core.crypto import TokenCipher, generate_key
from app.integrations.meta.instagram_client import InstagramClient

IG_ID = "17841400000000001"
APP_SECRET = "test-app-secret"


class FakeGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.sent: list[dict] = []
        self.send_error: tuple[int, dict] | None = None
        self.refresh_count = 0
        self.permissions = "instagram_business_basic,instagram_business_manage_messages"
        self.account_type = "MEDIA_CREATOR"
        self.profiles = {"igsid-mika": {"name": "Mika Sato", "username": "mika.s"}}
        # Unified Login knobs
        self.unified_scopes = ["instagram_basic", "instagram_manage_messages"]
        self.assigned: list[str] | None = [IG_ID]      # None → endpoint errors
        self.granular_targets: list[str] = [IG_ID]
        self.token_expires_in: int | None = None       # None → system-user token (never expires)
        self._mid = itertools.count(1)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path
        body: dict = {}
        if request.content:
            ctype = request.headers.get("content-type", "")
            if "json" in ctype:
                body = json.loads(request.content)
            else:
                body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.method, f"{url.host}{path}", {**dict(url.params), **body}))

        if url.host == "graph.facebook.com":
            return self.handle_facebook(request, path, dict(url.params), body)

        if url.host == "api.instagram.com" and path == "/oauth/access_token":
            if body.get("code") != "good-code":
                return httpx.Response(400, json={"error_type": "OAuthException", "code": 400,
                                                 "error_message": "Invalid authorization code"})
            return httpx.Response(200, json={"data": [{"access_token": "short-token", "user_id": IG_ID,
                                                       "permissions": self.permissions}]})
        if path == "/access_token":
            return httpx.Response(200, json={"access_token": "long-token-1", "token_type": "bearer",
                                             "expires_in": 5183944})
        if path == "/refresh_access_token":
            self.refresh_count += 1
            return httpx.Response(200, json={"access_token": f"long-token-r{self.refresh_count}",
                                             "token_type": "bearer", "expires_in": 5183944})
        if path.endswith("/me") and request.method == "GET":
            return httpx.Response(200, json={"user_id": IG_ID, "id": "app-scoped", "username": "my.creator",
                                             "account_type": self.account_type, "name": "Me"})
        if path.endswith("/me/subscribed_apps"):
            return httpx.Response(200, json={"success": True})
        if path.endswith(f"/{IG_ID}/messages") and request.method == "POST":
            if self.send_error:
                status, err = self.send_error
                return httpx.Response(status, json={"error": err})
            mid = f"aWdf{next(self._mid):06d}"
            self.sent.append({"to": body["recipient"]["id"], "text": body["message"]["text"], "mid": mid,
                              "auth": request.headers.get("authorization")})
            return httpx.Response(200, json={"recipient_id": body["recipient"]["id"], "message_id": mid})
        if path.endswith("/me/conversations"):
            return httpx.Response(200, json={"data": [{
                "id": "conv-1",
                "participants": {"data": [{"id": IG_ID, "username": "my.creator"},
                                          {"id": "igsid-mika", "username": "mika.s"}]},
                "messages": {"data": [
                    {"id": "m-old-2", "created_time": "2026-11-11T02:00:00+0000", "from": {"id": IG_ID},
                     "to": {"data": [{"id": "igsid-mika"}]}, "message": "sure!"},
                    {"id": "m-old-1", "created_time": "2026-11-11T01:59:00+0000", "from": {"id": "igsid-mika"},
                     "to": {"data": [{"id": IG_ID}]}, "message": "lunch tomorrow?"},
                ]},
            }]})
        igsid = path.rsplit("/", 1)[-1]
        if igsid in self.profiles:
            return httpx.Response(200, json=self.profiles[igsid])
        return httpx.Response(404, json={"error": {"message": f"unknown path {path}", "code": 100}})


    # ------------------------------------------------ graph.facebook.com (Unified Login)

    def handle_facebook(self, request: httpx.Request, path: str, params: dict, body: dict) -> httpx.Response:
        tail = path.split("/", 2)[-1]  # strip "/v26.0/"
        proof_ok = "appsecret_proof" in params
        if tail == "oauth/access_token":
            if params.get("grant_type") == "fb_exchange_token":
                return httpx.Response(200, json={"access_token": "EAA-long-user", "token_type": "bearer",
                                                 "expires_in": 5183944})
            if params.get("code") != "good-code":
                return httpx.Response(400, json={"error": {"message": "Invalid verification code format.",
                                                           "type": "OAuthException", "code": 100}})
            data = {"access_token": "EAA-system-user", "token_type": "bearer"}
            if self.token_expires_in:
                data = {"access_token": "EAA-short-user", "token_type": "bearer", "expires_in": self.token_expires_in}
            return httpx.Response(200, json=data)
        if tail == "debug_token":
            return httpx.Response(200, json={"data": {
                "app_id": "999", "type": "SYSTEM_USER", "is_valid": True, "expires_at": 0, "user_id": "sysuser-1",
                "scopes": self.unified_scopes,
                "granular_scopes": [{"scope": "instagram_manage_messages", "target_ids": self.granular_targets}],
            }})
        if not proof_ok:
            return httpx.Response(400, json={"error": {"message": "appsecret_proof missing", "code": 100}})
        if tail == "me":
            return httpx.Response(200, json={"id": "sysuser-1", "name": "ECHO System User"})
        if tail == "me/assigned_instagram_accounts":
            if self.assigned is None:
                return httpx.Response(400, json={"error": {"message": "Unsupported get request", "code": 100}})
            return httpx.Response(200, json={"data": [{"id": i} for i in self.assigned]})
        if tail == "me/accounts":
            return httpx.Response(200, json={"data": []})
        if tail == IG_ID:
            return httpx.Response(200, json={"id": IG_ID, "username": "my.creator", "name": "Me"})
        if tail == f"{IG_ID}/subscribed_apps":
            return httpx.Response(200, json={"success": True})
        if tail == f"{IG_ID}/messages" and request.method == "POST":
            if self.send_error:
                status, err = self.send_error
                return httpx.Response(status, json={"error": err})
            mid = f"aWdf{next(self._mid):06d}"
            self.sent.append({"to": body["recipient"]["id"], "text": body["message"]["text"], "mid": mid,
                              "auth": request.headers.get("authorization"), "host": "graph.facebook.com"})
            return httpx.Response(200, json={"recipient_id": body["recipient"]["id"], "message_id": mid})
        if tail in self.profiles:
            return httpx.Response(200, json=self.profiles[tail])
        return httpx.Response(404, json={"error": {"message": f"unknown path {path}", "code": 100}})


def make_service(fake: FakeGraph, settings, key: str | None = None):
    from app.services.instagram_service import InstagramService

    unified = settings.meta_login_mode == "unified"
    client = InstagramClient(app_id="999" if unified else "123", app_secret=APP_SECRET,
                             login_mode=settings.meta_login_mode, config_id="cfg-42" if unified else None,
                             graph_version="v26.0", http=httpx.AsyncClient(transport=fake.transport()))
    return InstagramService(settings=settings, client=client, cipher=TokenCipher(key or generate_key()))


def webhook_payload(*events: dict) -> dict:
    return {"object": "instagram", "entry": [{"id": IG_ID, "time": 1762828800000, "messaging": list(events)}]}


def inbound(mid: str, text: str, sender: str = "igsid-mika", ts: int | None = None) -> dict:
    import time

    return {"sender": {"id": sender}, "recipient": {"id": IG_ID},
            "timestamp": ts or int(time.time() * 1000), "message": {"mid": mid, "text": text}}


def echo(mid: str, text: str, recipient: str = "igsid-mika") -> dict:
    import time

    return {"sender": {"id": IG_ID}, "recipient": {"id": recipient},
            "timestamp": int(time.time() * 1000), "message": {"mid": mid, "text": text, "is_echo": True}}
