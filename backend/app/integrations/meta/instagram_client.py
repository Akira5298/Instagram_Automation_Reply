"""Instagram API with Instagram Login (Business Login for Instagram).

Endpoints (verified against Meta's docs, Nov 2026):
  authorize        https://www.instagram.com/oauth/authorize
  code → short     POST https://api.instagram.com/oauth/access_token   (≈1 hour; response wrapped in "data")
  short → long     GET  https://graph.instagram.com/access_token?grant_type=ig_exchange_token  (≈60 days)
  refresh          GET  https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token
                   (token must be ≥24 h old and unexpired)
  me               GET  https://graph.instagram.com/{v}/me?fields=user_id,username,account_type,name
  subscribe        POST https://graph.instagram.com/{v}/me/subscribed_apps?subscribed_fields=messages
  send             POST https://graph.instagram.com/{v}/{IG_ID}/messages  {"recipient":{"id"},"message":{"text"}}
  user profile     GET  https://graph.instagram.com/{v}/{IGSID}?fields=name,username
  conversations    GET  https://graph.instagram.com/{v}/me/conversations?platform=instagram

Permissions: instagram_business_basic, instagram_business_manage_messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

log = logging.getLogger("echo.instagram")

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_HOST = "https://graph.instagram.com"
SCOPES = ("instagram_business_basic", "instagram_business_manage_messages")

# Graph error codes that mean "try again later".
_TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 368, 613, 80002, 80006}


@dataclass(frozen=True)
class GraphError(Exception):
    status: int
    code: int | None
    subcode: int | None
    message: str
    trace_id: str | None = None

    def __str__(self) -> str:
        sub = f"/{self.subcode}" if self.subcode else ""
        return f"Instagram API error {self.code}{sub}: {self.message}"

    @property
    def is_transient(self) -> bool:
        return self.status >= 500 or self.status == 429 or (self.code in _TRANSIENT_CODES)

    @property
    def is_auth_error(self) -> bool:
        return self.code == 190 or self.status == 401

    @property
    def is_outside_window(self) -> bool:
        text = self.message.lower()
        # Matched on the message text; 2018278 is the subcode developers commonly
        # report for this case — confirm against your own logs.
        return self.subcode == 2018278 or "outside of allowed window" in text or "outside the allowed window" in text


@dataclass(frozen=True)
class TokenResult:
    access_token: str
    expires_in: int | None = None
    user_id: str | None = None
    permissions: list[str] | None = None


class InstagramClient:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        redirect_uri: str | None,
        graph_version: str = "v26.0",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.redirect_uri = redirect_uri
        self.version = graph_version
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))

    # ------------------------------------------------------------- OAuth

    def authorize_url(self, state: str) -> str:
        if not self.redirect_uri:
            raise RuntimeError("OAuth redirect URI is not configured")
        params = {
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": ",".join(SCOPES),
            "state": state,
            "enable_fb_login": "false",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> TokenResult:
        # Instagram appends "#_" to the code in the redirect; strip it defensively.
        code = code.split("#", 1)[0]
        data = await self._request("POST", TOKEN_URL, data={
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
            "code": code,
        })
        item = data["data"][0] if isinstance(data.get("data"), list) and data["data"] else data
        perms = item.get("permissions")
        if isinstance(perms, str):
            perms = [p.strip() for p in perms.split(",") if p.strip()]
        return TokenResult(access_token=item["access_token"], user_id=str(item.get("user_id") or ""),
                           permissions=perms)

    async def exchange_long_lived(self, short_token: str) -> TokenResult:
        data = await self._request("GET", f"{GRAPH_HOST}/access_token", params={
            "grant_type": "ig_exchange_token",
            "client_secret": self.app_secret,
            "access_token": short_token,
        })
        return TokenResult(access_token=data["access_token"], expires_in=data.get("expires_in"))

    async def refresh(self, long_token: str) -> TokenResult:
        data = await self._request("GET", f"{GRAPH_HOST}/refresh_access_token", params={
            "grant_type": "ig_refresh_token",
            "access_token": long_token,
        })
        return TokenResult(access_token=data["access_token"], expires_in=data.get("expires_in"))

    # ----------------------------------------------------------- Graph API

    async def me(self, token: str) -> dict[str, Any]:
        return await self._graph("GET", "me", token, params={"fields": "user_id,username,account_type,name"})

    async def subscribe_webhooks(self, token: str, fields: str = "messages") -> dict[str, Any]:
        return await self._graph("POST", "me/subscribed_apps", token, params={"subscribed_fields": fields})

    async def send_text(self, token: str, ig_id: str, recipient_igsid: str, text: str) -> str:
        data = await self._graph("POST", f"{ig_id}/messages", token, json={
            "recipient": {"id": recipient_igsid},
            "message": {"text": text},
        })
        return str(data["message_id"])

    async def user_profile(self, token: str, igsid: str) -> dict[str, Any]:
        return await self._graph("GET", igsid, token, params={"fields": "name,username"})

    async def conversations(self, token: str, limit: int = 20) -> list[dict[str, Any]]:
        data = await self._graph("GET", "me/conversations", token, params={
            "platform": "instagram",
            "fields": "id,updated_time,participants,messages.limit(20){id,created_time,from,to,message}",
            "limit": str(limit),
        })
        return data.get("data", [])

    # ------------------------------------------------------------ plumbing

    async def _graph(self, method: str, path: str, token: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{GRAPH_HOST}/{self.version}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"}
        return await self._request(method, url, headers=headers, **kwargs)

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.http.request(method, url, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or (isinstance(body, dict) and "error" in body):
            err = body.get("error", {}) if isinstance(body, dict) else {}
            if isinstance(err, str):  # api.instagram.com sometimes returns {"error_type", "error_message"}
                err = {"message": body.get("error_message") or err}
            if "error_message" in body and not err:
                err = {"message": body["error_message"], "code": body.get("code")}
            raise GraphError(
                status=response.status_code,
                code=err.get("code"),
                subcode=err.get("error_subcode"),
                message=str(err.get("message") or response.text[:200]),
                trace_id=err.get("fbtrace_id"),
            )
        return body if isinstance(body, dict) else {}

    async def aclose(self) -> None:
        await self.http.aclose()
